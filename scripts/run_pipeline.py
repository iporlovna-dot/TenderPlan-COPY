"""CLI: полный конвейер ТЗ → вердикт (шаги 5-8, plan.md §3).

Парсит документ ТЗ → извлекает требования (Claude) → сопоставляет с товаром (код).
Нужен ANTHROPIC_API_KEY (или профиль `ant auth login`).

Пример:
    python scripts/run_pipeline.py \
        data/samples/gloves_tender_01.docx \
        data/products/gloves_nitrile.json \
        data/profiles/gloves.json

Флаг --save data/requirements/out.json — сохранить извлечённые требования
(например, в data/golden/ как эталон для Этапа 0, plan.md §8).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from schema import Attribute, Hardness, Operator, Product, ReqType, Requirement, Status, Verdict  # noqa: E402
from parser import parse  # noqa: E402
from extractor import extract_requirements  # noqa: E402
from matcher import match  # noqa: E402
from keymatch import align_keys, align_values, apply_mapping  # noqa: E402

ICON = {Status.PASS: "✅", Status.VIOLATION: "❌", Status.GAP: "⚠️ "}
VERDICT_RU = {
    Verdict.ELIGIBLE: "ПОДХОДИТ",
    Verdict.ELIGIBLE_WITH_GAPS: "ПОДХОДИТ (есть пробелы на подтверждение)",
    Verdict.DISQUALIFIED: "НЕ ПОДХОДИТ (дисквалификация)",
}


def load_product(path):
    d = json.load(open(path, encoding="utf-8"))
    return Product(id=d["id"], name=d["name"],
                   attributes=[Attribute(**a) for a in d["attributes"]])


def to_requirements(raw_reqs):
    reqs = []
    for r in raw_reqs:
        reqs.append(Requirement(
            key=r["key"], operator=Operator(r["operator"]), value=r.get("value"),
            unit=r.get("unit"), hardness=Hardness(r.get("hardness", "soft")),
            type=ReqType(r.get("type", "technical")), raw=r.get("raw", ""),
        ))
    return reqs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tz")
    ap.add_argument("product")
    ap.add_argument("profile", nargs="?")
    ap.add_argument("--save", help="куда сохранить извлечённые требования (JSON)")
    args = ap.parse_args()

    profile = json.load(open(args.profile, encoding="utf-8")) if args.profile else None

    print("→ Парсинг ТЗ: %s" % args.tz)
    text = parse(args.tz)
    print("  извлечено %d символов текста" % len(text))

    print("→ Извлечение требований (Claude Sonnet 5)...")
    raw_reqs = extract_requirements(text, profile=profile)
    print("  извлечено %d требований" % len(raw_reqs))

    if args.save:
        purchase_id = os.path.splitext(os.path.basename(args.tz))[0]
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump({"purchase_id": purchase_id, "requirements": raw_reqs},
                      f, ensure_ascii=False, indent=2)
        print("  сохранено: %s" % args.save)

    product = load_product(args.product)
    # Семантический маппинг полей ТЗ→карточка по имени И значению (Haiku, keymatch.py).
    mapping = align_keys({r["key"]: r.get("value") for r in raw_reqs},
                         {a.key: a.value for a in product.attributes})
    if mapping:
        print("  маппинг ключей: %s" % ", ".join("%s→%s" % kv for kv in mapping.items()))
    aligned = align_values(apply_mapping(raw_reqs, mapping), product)  # семантика значений
    reqs = to_requirements(aligned)
    purchase_id = os.path.splitext(os.path.basename(args.tz))[0]
    res = match(product, reqs, purchase_id, profile.get("synonyms") if profile else None)

    print("=" * 70)
    print("ТОВАР:   %s" % product.name)
    print("ЗАКУПКА: %s" % purchase_id)
    print("=" * 70)
    for c in res.checks:
        print("%s %-32s %s" % (ICON[c.status], c.req.key, c.req.raw))
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
