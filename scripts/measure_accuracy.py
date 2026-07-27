"""Замер точности ядра на golden-наборе (Этап 0, гейт ≥90%).

Каждая фикстура tests/golden/*.json — замороженная пара (ТЗ ↔ товар) с экспертным вердиктом.
Метрика — БИНАРНАЯ дискриминация «подходит / не подходит» (это и есть ценность продукта:
не только принять свой товар, но и отклонить чужой):
  • label «не подходит»  = expected_verdict == disqualified;
  • label «подходит»     = eligible | eligible_with_gaps.
Движок прогоняется детерминированно (без LLM: align уже вшит в фикстуру), его вердикт
сводится к тому же бинарному классу и сверяется с меткой. Печатает таблицу, confusion и точность.

Запуск: .venv/bin/python scripts/measure_accuracy.py
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from matcher import match  # noqa: E402
from schema import (  # noqa: E402
    Attribute, Hardness, Operator, Product, ReqType, Requirement, Verdict,
)

GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "..", "tests", "golden")


def _product(pd):
    return Product(id=pd["id"], name=pd["name"],
                   attributes=[Attribute(**a) for a in pd["attributes"]])


def _reqs(rows):
    return [Requirement(key=r["key"], operator=Operator(r["operator"]), value=r.get("value"),
                        unit=r.get("unit"), hardness=Hardness(r.get("hardness", "soft")),
                        type=ReqType(r.get("type", "technical")), raw=r.get("raw", ""),
                        remapped=r.get("remapped", False),
                        remap_locked=r.get("remap_locked", False))
            for r in rows]


def _fits(verdict) -> bool:
    """Бинарный класс: товар ПОДХОДИТ = вердикт не «дисквалифицирован»."""
    return verdict != Verdict.DISQUALIFIED.value if isinstance(verdict, str) \
        else verdict != Verdict.DISQUALIFIED


def main():
    rows = []
    tp = tn = fp = fn = 0  # подходит=positive, не подходит=negative
    for fp_path in sorted(glob.glob(os.path.join(GOLDEN_DIR, "*.json"))):
        fx = json.load(open(fp_path, encoding="utf-8"))
        exp_fits = _fits(fx["expected_verdict"])            # метка эксперта
        res = match(_product(fx["product"]), _reqs(fx["requirements"]), fx["id"],
                    fx.get("synonyms"))
        eng_fits = _fits(res.verdict)                        # класс движка
        ok = exp_fits == eng_fits
        if exp_fits and eng_fits:
            tp += 1
        elif not exp_fits and not eng_fits:
            tn += 1
        elif not exp_fits and eng_fits:
            fp += 1
        else:
            fn += 1
        rows.append((ok, fx["id"][:46], "подходит" if exp_fits else "НЕ подходит",
                     res.verdict.value, res.score))

    total = len(rows)
    correct = sum(1 for r in rows if r[0])
    print("%-3s %-46s %-12s %-20s %4s" % ("", "фикстура", "эксперт", "движок", "%"))
    for ok, name, label, verdict, score in rows:
        print("%-3s %-46s %-12s %-20s %3d%%" % ("✓" if ok else "✗", name, label, verdict, score))
    pos = tp + fn  # всего «подходит»
    neg = tn + fp  # всего «не подходит»
    print("\nБаланс: подходит=%d, не подходит=%d (всего %d)" % (pos, neg, total))
    print("Confusion: TP=%d TN=%d FP=%d FN=%d  (FP=принял чужой, FN=отклонил свой)"
          % (tp, tn, fp, fn))
    print("Точность (accuracy): %d/%d = %.1f%%" % (correct, total, 100.0 * correct / total))
    if neg:
        print("Специфичность (отклонение чужого): %d/%d = %.1f%%" % (tn, neg, 100.0 * tn / neg))
    else:
        print("Специфичность: НЕТ негативов — дискриминация не измерена!")
    sys.exit(0 if correct == total else 1)


if __name__ == "__main__":
    main()
