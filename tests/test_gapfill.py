"""Тесты дозаполнения пробелов (src/gapfill.py): apply_fills + пересчёт вердикта. Без LLM/сети."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gapfill import apply_fills, gap_keys, recompute
from schema import Status


def check(name, ok):
    print(("  ✓ " if ok else "  ✗ ") + name)
    return ok


PRODUCT = {"id": "gloves", "name": "Перчатки нитриловые",
           "attributes": [{"key": "материал", "value": "нитрил", "status": "declared"}]}
REQS = [
    {"key": "материал", "operator": "eq", "value": "нитрил", "unit": "", "hardness": "soft",
     "type": "technical", "raw": "Материал - нитрил"},
    {"key": "толщина_мм", "operator": "gte", "value": 0.11, "unit": "мм", "hardness": "soft",
     "type": "technical", "raw": "Толщина не менее 0,11"},
]


def test_apply_fills():
    out = apply_fills(PRODUCT["attributes"], {"толщина_мм": 0.12})
    r = []
    r.append(check("новый атрибут добавлен", any(a["key"] == "толщина_мм" for a in out)))
    filled = next(a for a in out if a["key"] == "толщина_мм")
    r.append(check("значение клиента записано", filled["value"] == 0.12))
    r.append(check("статус confirmable (подтверждается клиентом)", filled["status"] == "confirmable"))
    r.append(check("помечен источник (doc)", "клиент" in filled["doc"].lower()))
    r.append(check("вход не мутирован", len(PRODUCT["attributes"]) == 1))
    r.append(check("существующий атрибут перезаписывается, не дублируется",
                   len(apply_fills(PRODUCT["attributes"], {"материал": "нитриловый"})) == 1))
    return r


def test_gap_then_fill_raises_score():
    res0, _ = recompute(PRODUCT, REQS, "t1")
    r = []
    r.append(check("толщина — пробел до дозаполнения", "толщина_мм" in gap_keys(res0)))
    res1, attrs = recompute(PRODUCT, REQS, "t1", fills={"толщина_мм": 0.12})
    r.append(check("после дозаполнения толщина не пробел", "толщина_мм" not in gap_keys(res1)))
    r.append(check("% вырос", res1.score > res0.score))
    thick = next(c for c in res1.checks if c.req.key == "толщина_мм")
    r.append(check("толщина проходит (0.12 ≥ 0.11)", thick.status == Status.PASS))
    return r


def test_fill_that_still_fails_is_honest():
    """Клиент ввёл значение, которое НЕ удовлетворяет требованию → движок не «зачитывает» вслепую."""
    res, _ = recompute(PRODUCT, REQS, "t1", fills={"толщина_мм": 0.05})  # 0.05 < 0.11
    thick = next(c for c in res.checks if c.req.key == "толщина_мм")
    r = []
    r.append(check("0.05 < 0.11 → НЕ проходит (честно)", thick.status != Status.PASS))
    r.append(check("это нарушение, а не пробел", thick.status == Status.VIOLATION))
    good, _ = recompute(PRODUCT, REQS, "t1", fills={"толщина_мм": 0.12})
    r.append(check("валидное значение даёт выше %, чем невалидное", good.score > res.score))
    return r


def main():
    r = test_apply_fills() + test_gap_then_fill_raises_score() + test_fill_that_still_fails_is_honest()
    passed = sum(r)
    print("\n%d/%d passed" % (passed, len(r)))
    sys.exit(0 if passed == len(r) else 1)


if __name__ == "__main__":
    main()
