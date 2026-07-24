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
