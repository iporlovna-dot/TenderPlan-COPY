"""Тесты ядра матчинга: проверяем все ветки логики, а не один happy-path."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from schema import (  # noqa: E402
    Attribute, Hardness, Operator, Product, ReqType, Requirement, Status, Verdict,
)
from matcher import field_kind, match  # noqa: E402

SYN = {"нитрил": ["нитрильный латекс", "нитриловый"]}


def _product(**kw):
    attrs = [Attribute(key=k, value=v) for k, v in kw.items()]
    return Product(id="p", name="p", attributes=attrs)


def _req(key, op, value=None, hardness="hard", rtype="technical", unit=None):
    return Requirement(key=key, operator=Operator(op), value=value,
                       hardness=Hardness(hardness), type=ReqType(rtype), unit=unit)


def test_synonym_material_pass():
    p = _product(материал="нитрил")
    r = [_req("материал", "eq", "нитрильный латекс")]
    res = match(p, r, "t", SYN)
    assert res.checks[0].status == Status.PASS


def test_gte_boundary_passes_and_flags():
    p = _product(толщина_пальцы_мм=0.11)
    r = [_req("толщина_пальцы_мм", "gte", 0.11, unit="мм")]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.PASS
    assert "впритык" in res.checks[0].note


def test_gte_below_threshold_violation():
    p = _product(толщина_пальцы_мм=0.10)
    r = [_req("толщина_пальцы_мм", "gte", 0.11, unit="мм")]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.VIOLATION
    assert res.verdict == Verdict.DISQUALIFIED  # hard-нарушение


def test_missing_attribute_is_gap_not_violation():
    p = _product(материал="нитрил")            # размеров нет
    r = [_req("размеры", "set", ["S", "M", "L"])]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.GAP
    assert res.verdict == Verdict.ELIGIBLE_WITH_GAPS  # пробел != дисквалификация


def test_set_cyrillic_latin_sizes_match():
    # товар «M» (лат.), ТЗ «М» (кир.) — визуально одинаковы, должны сойтись
    p = _product(размеры=["XS", "S", "M", "L"])          # латинские
    r = [_req("размеры", "set", ["XS", "S", "М"])]   # М кириллическая
    res = match(p, r, "t")
    assert res.checks[0].status == Status.PASS


def test_set_partial_coverage_is_gap():
    p = _product(размеры=["S", "M"])            # нет L
    r = [_req("размеры", "set", ["S", "M", "L"])]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.GAP
    assert "L" in res.checks[0].note


def test_wrong_material_disqualifies():
    p = _product(материал="нитрил")
    r = [_req("материал", "eq", "латекс")]      # требуется латекс
    res = match(p, r, "t")
    assert res.checks[0].status == Status.VIOLATION
    assert res.verdict == Verdict.DISQUALIFIED


def test_eq_number_in_string_with_unit_passes():
    # карточка «2,5 В» (строка, запятичная десятичная) ↔ требование 2.5 / «В»
    p = _product(напряжение="2,5 В")
    r = [_req("напряжение", "eq", 2.5, unit="В")]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.PASS


def test_eq_number_matches_but_unit_differs_violation():
    # то же число, но ЕДИНИЦА другая: «2,5 А» не должно подтвердить «2,5 В»
    p = _product(напряжение="2,5 А")
    r = [_req("напряжение", "eq", 2.5, unit="В")]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.VIOLATION


def test_eq_number_differs_violation():
    p = _product(напряжение="3 В")
    r = [_req("напряжение", "eq", 2.5, unit="В")]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.VIOLATION


def test_eq_unit_absent_on_card_is_lenient():
    # карточка без единицы — сверяем только число (частый случай неполной карточки)
    p = _product(напряжение="2,5")
    r = [_req("напряжение", "eq", 2.5, unit="В")]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.PASS


def test_gte_unit_mismatch_violation():
    # 3 ≥ 2.5 по числу, но «А» ≠ «В» → не проходит (иначе амперы «подтвердят» вольты)
    p = _product(ток="3 А")
    r = [_req("ток", "gte", 2.5, unit="В")]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.VIOLATION


def test_gte_comma_decimal_with_unit_passes():
    p = _product(толщина="0,11 мм")
    r = [_req("толщина", "gte", 0.11, unit="мм")]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.PASS


def test_one_of_synonym_match_passes():
    # ТЗ разрешает «Светодиодная лампа»; товар «LED» — то же по синонимам категории
    syn = {"светодиодная лампа": ["led", "светодиодная"]}
    p = _product(источник_света="LED")
    r = [_req("источник_света", "one_of", ["Ксеноновая лампа", "Светодиодная лампа"])]
    res = match(p, r, "t", syn)
    assert res.checks[0].status == Status.PASS


def test_one_of_no_match_violation():
    syn = {"светодиодная лампа": ["led"]}
    p = _product(источник_света="LED")
    r = [_req("источник_света", "one_of", ["Ксеноновая лампа", "Галогенная лампа"])]
    res = match(p, r, "t", syn)
    assert res.checks[0].status == Status.VIOLATION


def test_full_match_is_eligible_100():
    p = _product(материал="нитрил", размеры=["S", "M", "L"], рег_удостоверение="есть")
    r = [_req("материал", "eq", "нитрильный латекс"),
         _req("размеры", "set", ["S", "M", "L"]),
         _req("рег_удостоверение", "present", True, rtype="documentary")]
    res = match(p, r, "t", SYN)
    assert res.score == 100
    assert res.verdict == Verdict.ELIGIBLE


def test_eq_single_value_against_product_size_set():
    # товар в размерах ['0'..'4'], ТЗ просит «№3» → проходит (входит в набор), не violation
    p = _product(размеры=["0", "1", "2", "3", "4"])
    r = [_req("размеры", "eq", "№3")]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.PASS


def test_eq_single_value_not_in_product_set_violation():
    # ТЗ просит «№7», которого нет в наборе товара → violation
    p = _product(размеры=["0", "1", "2", "3", "4"])
    r = [_req("размеры", "eq", "№7")]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.VIOLATION


def test_field_kind_supply_fields_are_documentary():
    # поставочные/бумажные поля — есть у любого медизделия, не несут категорийного сигнала
    for k in ("рег_удостоверение", "ру_имеется", "срок_годности_остаточный_мес",
              "новизна_товара", "документы_соответствия", "сертификат_декларация_соответствия",
              "инструкция_на_русском", "маркировка_русский", "разрешён_в_рф", "гарантийный_срок"):
        assert field_kind(k) == ReqType.DOCUMENTARY, k


def test_field_kind_category_fields_are_technical():
    # категорийные признаки товара — технические
    for k in ("тип_клинка", "диаметр_световода_мм", "источник_света", "материал",
              "напряжение_в", "размеры", "форма"):
        assert field_kind(k) == ReqType.TECHNICAL, k


def test_supply_fields_do_not_win_score_alone():
    # позиция, где товар совпал ТОЛЬКО по поставочным полям, не должна давать высокий %:
    # напряжение (техническое) расходится → низкий взвешенный %, категорийного совпадения нет
    p = _product(рег_удостоверение="РУ имеется", срок_годности_остаточный_мес=12, напряжение_в=2.5)
    reqs = [_req("рег_удостоверение", "present", None, rtype="documentary"),
            _req("срок_годности_остаточный_мес", "gte", 6, rtype="documentary"),
            _req("напряжение_в", "eq", 3.5, rtype="technical")]
    res = match(p, reqs, "t")
    tech_pass = sum(1 for c in res.checks
                    if c.status == Status.PASS and c.req.type == ReqType.TECHNICAL)
    assert tech_pass == 0                     # категорийных совпадений нет
    assert res.score < 60                     # одни бумаги не тянут на «покрыто»


def test_resolution_gte_not_false_unit_violation():
    # разрешение «640 x 480 (RGB)» vs ТЗ «не менее 640x480»: «x» — знак умножения, не единица,
    # раньше читался единицей → ложная несовместимость «x»≠«пиксель» → ложная дисквалификация
    p = _product(разрешение_дисплея="640 x 480 (RGB)")
    r = [_req("разрешение_дисплея", "gte", "640x480", unit="пиксель")]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.PASS
    assert res.verdict != Verdict.DISQUALIFIED


def test_unit_scale_mah_vs_ah_range_passes():
    # 1350 мАч попадает в диапазон [1.35, 3.5] А·ч (одно семейство, разный масштаб)
    p = _product(ёмкость_аккумулятора="1350 мАч")
    r = [_req("ёмкость_аккумулятора", "range", [1.35, 3.5], unit="Ампер-час")]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.PASS


def test_unit_scale_mm_vs_cm_gte():
    # 130 мм ≥ 12 см (=120 мм)
    p = _product(длина="130 мм")
    r = [_req("длина", "gte", 12, unit="см")]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.PASS


def test_unit_scale_eq_mah_ah():
    p = _product(ёмкость="1.35 А·ч")
    r = [_req("ёмкость", "eq", 1350, unit="мАч")]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.PASS


def test_unit_scale_different_family_still_violation():
    # разные семейства (заряд vs длина) не примиряются
    p = _product(x="5 мм")
    r = [_req("x", "gte", 2, unit="А·ч")]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.VIOLATION


def test_dimension_value_no_spurious_unit():
    from matcher import _num_unit
    assert _num_unit("640 x 480")[1] == ""       # «x» не единица
    assert _num_unit("87 х 20 х 62")[1] == ""     # кириллическая «х» тоже
    assert _num_unit("2,5 В")[1] == "b"           # настоящая единица цела


def test_range_against_list_any_element_passes():
    # ТЗ: «клинок Макинтош с длиной 110-143 мм»; товар — набор длин, подходит хотя бы один
    p = _product(длина_клинка_макинтош=["10.45 см", "12.11 см", "14.25 см", "16.08 см"])
    r = [_req("длина_клинка_макинтош", "range", [110, 143], unit="мм")]  # 142,5 попадает
    res = match(p, r, "t")
    assert res.checks[0].status == Status.PASS


def test_range_against_list_none_matches_violation():
    p = _product(длина_клинка_макинтош=["10.45 см", "12.11 см"])  # 104.5, 121.1 — вне [130,161]
    r = [_req("длина_клинка_макинтош", "range", [130, 161], unit="мм")]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.VIOLATION


def test_gte_against_list_any_element_passes():
    p = _product(ширина=["12", "23", "23"])
    r = [_req("ширина", "gte", 20)]  # 23 >= 20
    res = match(p, r, "t")
    assert res.checks[0].status == Status.PASS


def test_resolution_pixelcount_gte_passes():
    # ТЗ задаёт разрешение числом пикселей (1280*720=921600), карточка размерами
    p = _product(разрешение_камеры="1280 x 720")
    r = [_req("разрешение_камеры", "gte", 921600)]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.PASS


def test_resolution_pixelcount_eq_passes():
    p = _product(разрешение_дисплея="640 x 480 (RGB)")
    r = [_req("разрешение_дисплея", "eq", 307200)]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.PASS


def test_three_dim_not_treated_as_pixel_product():
    # габариты «87 x 20 x 62» (3 числа) НЕ дают произведение → gte 100 не проходит по первому числу
    p = _product(габариты="87 x 20 x 62")
    r = [_req("габариты", "gte", 100)]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.VIOLATION


def test_blade_set_coverage_pass_and_gap():
    p = _product(типы_клинков=["макинтош", "миллер", "d-blade"])
    # покрыто → pass
    ok = match(p, [_req("типы_клинков", "eq", ["макинтош", "миллер"])], "t")
    assert ok.checks[0].status == Status.PASS
    # непокрыто → GAP, не VIOLATION (набор не дисквалифицирует)
    miss = match(p, [_req("типы_клинков", "eq", ["макинтош", "ксенон"])], "t")
    assert miss.checks[0].status == Status.GAP
    assert miss.verdict != Verdict.DISQUALIFIED


def test_eq_boolean_true_is_present_not_literal():
    # требование «наличие» пришло булевым true → карточка с осмысленным значением проходит,
    # не должно сравниваться строкой «новый…» == «true» (это давало ложное нарушение)
    p = _product(новизна_товара="новый, не бывший в употреблении")
    r = [_req("новизна_товара", "eq", True)]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.PASS
    assert res.verdict != Verdict.DISQUALIFIED


def test_eq_boolean_true_negation_value_violation():
    # если карточка явно отрицает («нет») — булево-true требование не проходит
    p = _product(функция="нет")
    r = [_req("функция", "eq", True)]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.VIOLATION


def _remapped_req(key, op, value, locked=False, hardness="hard"):
    r = _req(key, op, value, hardness=hardness)
    r.remapped = True
    r.remap_locked = locked
    return r


def test_remapped_violation_downgrades_to_gap():
    # ключ пришёл из семантического маппинга (align_keys), значение не сошлось и НЕ critical
    # → ложный маппинг не должен дисквалифицировать: violation деградирует в gap (plan §3.6в)
    p = _product(материал_рукояти="металл")
    r = [_remapped_req("материал_рукояти", "eq", "На рукоятке")]  # ложно легло из «конструкция»
    res = match(p, r, "t")
    assert res.checks[0].status == Status.GAP
    assert res.verdict != Verdict.DISQUALIFIED


def test_remapped_locked_still_disqualifies():
    # маппинг на critical_attribute заперт (remap_locked): реальное расхождение источника света
    # остаётся нарушением и дисквалифицирует
    p = _product(тип_освещения="светодиодный")
    r = [_remapped_req("тип_освещения", "eq", "галоген", locked=True)]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.VIOLATION
    assert res.verdict == Verdict.DISQUALIFIED


def test_non_remapped_violation_stays_violation():
    # дословный (не remapped) ключ с расхождением — по-прежнему нарушение (поведение не изменилось)
    p = _product(материал="полиамид")
    r = [_req("материал", "eq", "нержавеющая сталь")]
    res = match(p, r, "t")
    assert res.checks[0].status == Status.VIOLATION
    assert res.verdict == Verdict.DISQUALIFIED


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn()
            print("✅ %s" % fn.__name__)
            passed += 1
        except Exception:
            print("❌ %s" % fn.__name__)
            traceback.print_exc()
    print("\n%d/%d тестов прошли" % (passed, len(fns)))
    sys.exit(0 if passed == len(fns) else 1)
