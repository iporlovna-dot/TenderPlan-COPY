"""CLI: прогнать сопоставление товара с требованиями закупки.

Пример:
    python scripts/run_match.py \
        data/products/gloves_nitrile.json \
        data/requirements/gloves_tender_01.json \
        data/profiles/gloves.json
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from schema import (  # noqa: E402
    Attribute, Hardness, Operator, Product, ReqType, Requirement, Status, Verdict,
)
from matcher import match  # noqa: E402

ICON = {Status.PASS: "✅", Status.VIOLATION: "❌", Status.GAP: "⚠️ "}
VERDICT_RU = {
    Verdict.ELIGIBLE: "ПОДХОДИТ",
    Verdict.ELIGIBLE_WITH_GAPS: "ПОДХОДИТ (есть пробелы на подтверждение)",
    Verdict.DISQUALIFIED: "НЕ ПОДХОДИТ (дисквалификация)",
}


def load_product(path):
    d = json.load(open(path, encoding="utf-8"))
    attrs = [Attribute(**a) for a in d["attributes"]]
    return Product(id=d["id"], name=d["name"], attributes=attrs)


def load_requirements(path):
    d = json.load(open(path, encoding="utf-8"))
    reqs = []
    for r in d["requirements"]:
        reqs.append(Requirement(
            key=r["key"], operator=Operator(r["operator"]), value=r.get("value"),
            unit=r.get("unit"), hardness=Hardness(r.get("hardness", "soft")),
            type=ReqType(r.get("type", "technical")), raw=r.get("raw", ""),
        ))
    return d["purchase_id"], reqs


def main():
    product = load_product(sys.argv[1])
    purchase_id, reqs = load_requirements(sys.argv[2])
    synonyms = None
    if len(sys.argv) > 3:
        synonyms = json.load(open(sys.argv[3], encoding="utf-8")).get("synonyms")

    res = match(product, reqs, purchase_id, synonyms)

    print("=" * 70)
    print("ТОВАР:   %s" % product.name)
    print("ЗАКУПКА: %s" % purchase_id)
    print("=" * 70)
    for c in res.checks:
        line = "%s %-32s %s" % (ICON[c.status], c.req.key, c.req.raw)
        print(line)
        if c.note:
            print("       └─ %s" % c.note)
        if c.action:
            print("       └─ действие: %s" % c.action)
    print("-" * 70)
    n_pass = sum(1 for c in res.checks if c.status == Status.PASS)
    n_gap = sum(1 for c in res.checks if c.status == Status.GAP)
    n_viol = sum(1 for c in res.checks if c.status == Status.VIOLATION)
    print("Готовность заявки:   %d%%" % res.score)
    print("Проверок:            %d  (✅ %d  ⚠️ %d  ❌ %d)" %
          (len(res.checks), n_pass, n_gap, n_viol))
    print("ВЕРДИКТ:             %s" % VERDICT_RU[res.verdict])
    print()
    print(res.explanation)
    print("=" * 70)


if __name__ == "__main__":
    main()
