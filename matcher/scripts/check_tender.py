"""Проверка ОДНОЙ котировки под товар: «проходит / не проходит» + разбор (plan.md Этап 0).

Единая команда для рабочего цикла «клиент кидает закупку — сервис даёт вердикт».
Источник ТЗ — на выбор:
  • --id <tender_id>   забрать с Тендерплана (позиции, КТРУ, вложения); для многолота
                       сам находит позицию товара по КТРУ и извлекает ТОЛЬКО её требования (§3.4a)
  • --tz <файл|папка>  локальный ТЗ (.docx/.pdf/.xlsx/.txt); можно вручную сузить --position

Прогоняет: парсинг → извлечение (Claude) → семантика (align_keys/align_values) → матчинг.
Печатает вердикт, %, и построчно pass/violation/gap. С --golden сохраняет детерминированную
регресс-фикстуру (снимок карточки+синонимы+требования+ожид. результат) в формате tests/golden.

Примеры:
    .venv/bin/python scripts/check_tender.py --id 6a54c62c3951804ff38c1497 \
        --product data/products/ophthalmoscope_eurolight_e36.json \
        --profile data/profiles/ophthalmoscope.json

    .venv/bin/python scripts/check_tender.py --tz "/path/ТЗ.docx" \
        --product data/products/ophthalmoscope_eurolight_e36.json \
        --profile data/profiles/ophthalmoscope.json --golden tests/golden/ophth_new.json

Ключи в окружении: TENDERPLAN_TOKEN (для --id), ANTHROPIC_API_KEY (извлечение).
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from schema import Attribute, Hardness, Operator, Product, ReqType, Requirement, Verdict  # noqa: E402
from parser import parse  # noqa: E402
from extractor import extract_requirements  # noqa: E402
from keymatch import align_keys, align_values, apply_mapping  # noqa: E402
from matcher import match  # noqa: E402
from ktru import best_position  # noqa: E402

PARSE_EXT = (".docx", ".doc", ".pdf", ".xlsx", ".xls", ".txt", ".md", ".zip", ".rar")
VERDICT_RU = {Verdict.ELIGIBLE: "ПРОХОДИТ", Verdict.ELIGIBLE_WITH_GAPS: "ПРОХОДИТ (есть пробелы)",
              Verdict.DISQUALIFIED: "НЕ ПРОХОДИТ"}
# файлы, где обычно лежат сами требования (а не НМЦК/контракт/заявка) — берём их приоритетно
TZ_HINTS = ("описание объекта", "техническ", "тз ", "т.з", "характеристик")


def load_product(path):
    d = json.load(open(path, encoding="utf-8"))
    return d, Product(id=d["id"], name=d["name"], attributes=[Attribute(**a) for a in d["attributes"]])


def to_requirements(rows):
    return [Requirement(key=r["key"], operator=Operator(r["operator"]), value=r.get("value"),
                        unit=r.get("unit"), hardness=Hardness(r.get("hardness", "soft")),
                        type=ReqType(r.get("type", "technical")), raw=r.get("raw", ""),
                        remapped=r.get("remapped", False), remap_locked=r.get("remap_locked", False))
            for r in rows]


def _pick_tz_files(files):
    """Из вложений выбрать файлы ТЗ: приоритет — «Описание объекта»/«ТЗ»; иначе все читаемые."""
    parseable = [f for f in files if f.lower().endswith(PARSE_EXT)]
    hinted = [f for f in parseable if any(h in os.path.basename(f).lower() for h in TZ_HINTS)]
    return hinted or parseable


def _tz_from_id(tender_id, product_dict):
    """Забрать ТЗ с Тендерплана + определить позицию товара в лоте (для скоупа §3.4a)."""
    import tempfile
    from tenderplan import TenderplanSource
    src = TenderplanSource()
    try:
        purchase = src.get_tender(tender_id)
        positions = src.positions(tender_id)
        pos = None
        if len(positions) > 1:
            idx = best_position(product_dict.get("ktru", []), [p.code for p in positions])
            if idx is not None:
                pos = {"name": positions[idx].name,
                       "others": [p.name for j, p in enumerate(positions) if j != idx]}
            print("  лот: %d позиций%s" % (
                len(positions), " → позиция товара «%s»" % pos["name"] if pos else " (позиция товара не определена по КТРУ)"))
        with tempfile.TemporaryDirectory() as tmp:
            files = src.download_attachments(purchase, tmp)
            tz_files = _pick_tz_files(files)
            print("  вложения ТЗ:", [os.path.basename(f) for f in tz_files] or "нет читаемых")
            texts = []
            for f in tz_files:
                try:
                    texts.append(parse(f))
                except Exception as e:
                    print("    парсинг пропущен (%s): %s" % (os.path.basename(f), e))
            return purchase.subject, "\n\n".join(texts), pos
    finally:
        src.close()


def _tz_from_local(path, position_name, others):
    files = [path] if os.path.isfile(path) else glob.glob(os.path.join(path, "*"))
    texts = []
    for f in _pick_tz_files(files):
        try:
            texts.append(parse(f))
        except Exception as e:
            print("    парсинг пропущен (%s): %s" % (os.path.basename(f), e))
    pos = {"name": position_name, "others": others or []} if position_name else None
    return os.path.basename(path), "\n\n".join(texts), pos


def main():
    ap = argparse.ArgumentParser(description="Проверить котировку под товар")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--id", help="id тендера Тендерплана")
    g.add_argument("--tz", help="локальный файл/папка ТЗ")
    ap.add_argument("--product", required=True, help="карточка товара .json")
    ap.add_argument("--profile", required=True, help="профиль категории .json")
    ap.add_argument("--position", help="(для --tz) имя позиции товара в многолоте — сузить извлечение")
    ap.add_argument("--others", help="(для --tz) прочие позиции лота через ; — исключить")
    ap.add_argument("--golden", help="сохранить регресс-фикстуру сюда (формат tests/golden)")
    args = ap.parse_args()

    profile = json.load(open(args.profile, encoding="utf-8"))
    pd, product = load_product(args.product)

    if args.id:
        subject, tz, pos = _tz_from_id(args.id, pd)
    else:
        subject, tz, pos = _tz_from_local(
            args.tz, args.position, (args.others.split(";") if args.others else None))

    print("Закупка:", (subject or "")[:80])
    print("Товар:", product.id, "| ТЗ:", len(tz), "символов")
    if not tz.strip():
        print("НЕТ ЧИТАЕМОГО ТЗ (архивы/.doc/скан/SSL) — проверить нечего"); sys.exit(2)

    raw = extract_requirements(tz, profile=profile, position=pos)
    req_fields = {r["key"]: r.get("value") for r in raw}
    mapping = align_keys(req_fields, {a.key: a.value for a in product.attributes},
                         key_synonyms=profile.get("key_synonyms"))
    aligned = align_values(apply_mapping(raw, mapping, profile.get("critical_attributes")), product)
    res = match(product, to_requirements(aligned), args.id or args.tz, profile.get("synonyms"))

    print("\n=== %s | %d%% ===" % (VERDICT_RU[res.verdict], res.score))
    for c in res.checks:
        mark = {"pass": "✓", "violation": "✗", "gap": "·"}.get(c.status.value, "?")
        print("  %s %-32s тр=%s" % (mark, c.req.key, str(c.req.value)[:40]))
    print("\n" + res.explanation)

    if args.golden:
        fix = {"id": os.path.splitext(os.path.basename(args.golden))[0],
               "source": "check_tender: %s" % (args.id or args.tz),
               "expected_verdict": res.verdict.value, "expected_score": res.score, "score_tolerance": 0,
               "synonyms": profile.get("synonyms"), "product": pd, "requirements": aligned}
        json.dump(fix, open(args.golden, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("\ngolden-фикстура сохранена:", args.golden, "(проверь вердикт глазами перед фиксацией)")


if __name__ == "__main__":
    main()
