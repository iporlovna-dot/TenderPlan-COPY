"""Тесты ядра матчинга: проверяем все ветки логики, а не один happy-path."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from schema import (  # noqa: E402
    Attribute, Hardness, Operator, Product, ReqType, Requirement, Status, Verdict,
)
from matcher import match  # noqa: E402

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


def test_full_match_is_eligible_100():
    p = _product(материал="нитрил", размеры=["S", "M", "L"], рег_удостоверение="есть")
    r = [_req("материал", "eq", "нитрильный латекс"),
         _req("размеры", "set", ["S", "M", "L"]),
         _req("рег_удостоверение", "present", True, rtype="documentary")]
    res = match(p, r, "t", SYN)
    assert res.score == 100
    assert res.verdict == Verdict.ELIGIBLE


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
