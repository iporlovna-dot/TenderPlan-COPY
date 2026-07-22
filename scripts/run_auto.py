"""Сквозной АВТОМАТИЧЕСКИЙ прогон воронки: источник → ТЗ → вердикты (plan.md §3).

Единая точка входа. Никаких ручных id: закупки берутся из источника (Тендерплан по
ключу категории), ТЗ качаются, парсятся, из них извлекаются требования (Claude),
и каждая закупка сопоставляется с номенклатурой поставщика. На выходе — лента вердиктов.

Запускается по расписанию (cron / systemd timer) без участия человека:
    */30 * * * *  cd /path && .venv/bin/python scripts/run_auto.py \
                     data/profiles/gloves.json data/products/ --out data/results

Нужны два ключа в окружении (разово, см. plan.md §11 ШАГ 0):
    TENDERPLAN_TOKEN  — PAT со scope resources + keys + relations (+marks)
    ANTHROPIC_API_KEY — для извлечения требований (шаг 6)
"""
import argparse
import glob
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from schema import Attribute, Hardness, Operator, Product, ReqType, Requirement, Status, Verdict  # noqa: E402
from tenderplan import TenderplanSource  # noqa: E402
from filter import score_purchase, in_region, deadline_in_window  # noqa: E402
from parser import parse  # noqa: E402
from extractor import extract_requirements  # noqa: E402
from matcher import match  # noqa: E402
from ktru import ktru_relation  # noqa: E402
from keymatch import align_keys, apply_mapping  # noqa: E402

VERDICT_RU = {
    Verdict.ELIGIBLE: "ПОДХОДИТ",
    Verdict.ELIGIBLE_WITH_GAPS: "ПОДХОДИТ (есть пробелы)",
    Verdict.DISQUALIFIED: "НЕ ПОДХОДИТ",
}
PARSE_EXT = (".docx", ".pdf", ".xlsx", ".xls", ".txt", ".md")  # что умеет parser.py


def load_products(path):
    files = [path] if path.endswith(".json") else glob.glob(os.path.join(path, "*.json"))
    products = []
    for fp in files:
        d = json.load(open(fp, encoding="utf-8"))
        products.append(Product(id=d["id"], name=d["name"],
                                attributes=[Attribute(**a) for a in d["attributes"]]))
    return products


def load_product_codes(path):
    """Коды КТРУ всей номенклатуры (для сверки товар↔позиция закупки в воронке)."""
    files = [path] if path.endswith(".json") else glob.glob(os.path.join(path, "*.json"))
    codes = set()
    for fp in files:
        codes.update(json.load(open(fp, encoding="utf-8")).get("ktru", []))
    return codes


def to_requirements(raw):
    return [Requirement(key=r["key"], operator=Operator(r["operator"]), value=r.get("value"),
                        unit=r.get("unit"), hardness=Hardness(r.get("hardness", "soft")),
                        type=ReqType(r.get("type", "technical")), raw=r.get("raw", ""))
            for r in raw]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("profile", help="профиль категории, напр. data/profiles/gloves.json")
    ap.add_argument("products", help="карточка товара .json или каталог с ними")
    ap.add_argument("--out", default="data/results", help="куда писать вердикты")
    ap.add_argument("--limit", type=int, default=50, help="максимум закупок за прогон")
    ap.add_argument("--min-score", type=float, default=1.0, help="порог грубого фильтра (КТРУ/слова)")
    ap.add_argument("--regions", help="коды регионов через запятую (напр. 77,54); пусто = вся РФ")
    ap.add_argument("--deadline-min", type=float, default=1, help="мин. дней до окончания подачи")
    ap.add_argument("--deadline-max", type=float, default=29, help="макс. дней до окончания подачи")
    ap.add_argument("--collect-only", action="store_true",
                    help="только сбор+парсинг ТЗ (работает на подписке Тендерплана, БЕЗ Anthropic); "
                         "тексты ТЗ складываются в --out, извлечение и вердикты пропускаются")
    args = ap.parse_args()

    profile = json.load(open(args.profile, encoding="utf-8"))
    products = [] if args.collect_only else load_products(args.products)
    prod_codes = set() if args.collect_only else load_product_codes(args.products)
    os.makedirs(args.out, exist_ok=True)
    mode = "СБОР ТЗ (без Anthropic)" if args.collect_only else "полный (с вердиктами)"
    print("Профиль: %s | товаров: %d | режим: %s" %
          (profile.get("category"), len(products), mode))

    regions = [r.strip() for r in args.regions.split(",")] if args.regions else None
    print("Фильтры: срок подачи %g–%g дн | регионы: %s" %
          (args.deadline_min, args.deadline_max, ",".join(regions) if regions else "вся РФ"))

    source = TenderplanSource()
    processed = 0
    skipped_deadline = skipped_region = skipped_ktru = 0

    # Воронка (дёшево → дорого): КТРУ/слова → срок подачи → регион → анализ ТЗ.
    for purchase in source.iter_new(profile):
        if processed >= args.limit:
            break
        # Шаг 2а: грубый фильтр по ОКПД2/КТРУ/словам.
        if score_purchase(purchase, profile) < args.min_score:
            continue
        # Шаг 2б: срок подачи — только активные, где ещё можно подать заявку.
        if not deadline_in_window(purchase, args.deadline_min, args.deadline_max):
            skipped_deadline += 1
            continue
        # Шаг 2в: регион поставки.
        if not in_region(purchase, regions):
            skipped_region += 1
            continue
        # Шаг 2г: сверка КТРУ товар↔позиции закупки (ktru.py) — отсечь чужие группы до LLM.
        # Позиции берём из fullinfo (дешёвый запрос). Нет кодов/структуры → не отсекаем.
        rel = "group"
        if prod_codes:
            pos_codes = source.position_ktru(purchase.id)
            rel = ktru_relation(prod_codes, pos_codes) if pos_codes else "group"
            if rel == "none":
                skipped_ktru += 1
                continue
        processed += 1
        ktru_mark = {"exact": "  [КТРУ: точная позиция]", "group": "  [КТРУ: та же группа]"}.get(rel, "")
        print("\n[%d] %s — %s%s" % (processed, purchase.id, purchase.subject[:60], ktru_mark))

        # Шаг 4-5: скачать вложения ТЗ и распарсить в текст.
        with tempfile.TemporaryDirectory() as tmp:
            files = source.download_attachments(purchase, tmp)
            texts = []
            for fp in files:
                if fp.lower().endswith(PARSE_EXT):
                    try:
                        texts.append(parse(fp))
                    except Exception as e:
                        print("    парсинг пропущен (%s): %s" % (os.path.basename(fp), e))
            if not texts:
                print("    нет читаемых ТЗ (архивы/.doc/сканы) — пропуск")
                continue
            tz_text = "\n\n".join(texts)

        # Режим сбора: сохраняем текст ТЗ и НЕ трогаем Anthropic (работает на подписке).
        if args.collect_only:
            tz_path = os.path.join(args.out, "%s.txt" % purchase.id)
            with open(tz_path, "w", encoding="utf-8") as f:
                f.write(tz_text)
            print("    текст ТЗ сохранён: %s (%d симв.)" % (tz_path, len(tz_text)))
            continue

        # Шаг 6: извлечь требования (Claude). Шаг 7-8: сопоставить с каждым товаром.
        raw_reqs = extract_requirements(tz_text, profile=profile)
        req_fields = {r["key"]: r.get("value") for r in raw_reqs}
        for product in products:
            # Семантический маппинг полей ТЗ→карточка по имени И значению (Haiku, keymatch.py).
            mapping = align_keys(req_fields, {a.key: a.value for a in product.attributes})
            reqs = to_requirements(apply_mapping(raw_reqs, mapping))
            res = match(product, reqs, purchase.id, profile.get("synonyms"))
            print("    %-28s %3d%%  %s" % (product.id, res.score, VERDICT_RU[res.verdict]))
            out = os.path.join(args.out, "%s__%s.json" % (purchase.id, product.id))
            with open(out, "w", encoding="utf-8") as f:
                json.dump({
                    "purchase_id": purchase.id, "subject": purchase.subject,
                    "product_id": product.id, "score": res.score,
                    "verdict": res.verdict.value, "explanation": res.explanation,
                    "checks": [{"req": c.req.key, "status": c.status.value,
                                "note": c.note, "action": c.action} for c in res.checks],
                }, f, ensure_ascii=False, indent=2)

    source.close()
    print("\nГотово. Обработано закупок: %d (отсеяно по сроку: %d, по региону: %d, по КТРУ: %d). "
          "Вердикты в %s/" %
          (processed, skipped_deadline, skipped_region, skipped_ktru, args.out))


if __name__ == "__main__":
    main()
