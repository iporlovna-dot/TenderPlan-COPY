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
from ktru import ktru_relation, best_position  # noqa: E402
from keymatch import align_keys, align_values, apply_mapping  # noqa: E402

VERDICT_RU = {
    Verdict.ELIGIBLE: "ПОДХОДИТ",
    Verdict.ELIGIBLE_WITH_GAPS: "ПОДХОДИТ (есть пробелы)",
    Verdict.DISQUALIFIED: "НЕ ПОДХОДИТ",
}
PARSE_EXT = (".docx", ".doc", ".pdf", ".xlsx", ".xls", ".txt", ".md", ".zip", ".rar")  # что умеет parser.py


def load_products(path):
    files = [path] if path.endswith(".json") else glob.glob(os.path.join(path, "*.json"))
    products = []
    for fp in files:
        d = json.load(open(fp, encoding="utf-8"))
        products.append(Product(id=d["id"], name=d["name"],
                                attributes=[Attribute(**a) for a in d["attributes"]]))
    return products


def load_product_codes_by_id(path):
    """Коды КТРУ по каждому товару: {product_id: [codes]}.

    Общий набор (для КТРУ-фильтра воронки) собирается из значений; коды конкретного
    товара нужны, чтобы определить ЕГО позицию в многолоте (пометка «позиция N из M»).
    """
    files = [path] if path.endswith(".json") else glob.glob(os.path.join(path, "*.json"))
    by_id = {}
    for fp in files:
        d = json.load(open(fp, encoding="utf-8"))
        by_id[d["id"]] = d.get("ktru", [])
    return by_id


def lot_placement(product_id, codes_by_id, positions):
    """Где товар в сборном лоте: «позиция N из M» + что ещё в лоте (§11.4).

    None для одиночных лотов (M≤1) и когда позиция товара не определяется по КТРУ —
    чтобы не засорять вердикт по чистым закупкам. Данные о позициях уже пришли из
    источника (Тендерплан `fullinfo`), LLM не нужен.
    """
    if len(positions) <= 1:
        return None
    idx = best_position(codes_by_id.get(product_id, []), [p.code for p in positions])
    if idx is None:
        return None
    return {
        "position": idx + 1,
        "total": len(positions),
        "name": positions[idx].name,
        "quantity": positions[idx].quantity,
        "others": [p.name for j, p in enumerate(positions) if j != idx],
    }


def to_requirements(raw):
    return [Requirement(key=r["key"], operator=Operator(r["operator"]), value=r.get("value"),
                        unit=r.get("unit"), hardness=Hardness(r.get("hardness", "soft")),
                        type=ReqType(r.get("type", "technical")), raw=r.get("raw", ""),
                        remapped=r.get("remapped", False), remap_locked=r.get("remap_locked", False))
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
    codes_by_id = {} if args.collect_only else load_product_codes_by_id(args.products)
    prod_codes = set().union(*codes_by_id.values()) if codes_by_id else set()
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
        # Позиции берём из fullinfo (один дешёвый запрос): их коды — для КТРУ-фильтра,
        # а сами позиции — для пометки «позиция N из M» многолотов ниже. Нет кодов → не отсекаем.
        rel = "group"
        positions = []
        if prod_codes:
            positions = source.positions(purchase.id)
            pos_codes = [p.code for p in positions if p.code]
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

        # Шаг 6: извлечь требования (Claude), СКОУП по позиции товара в многолоте (§3.4a).
        # Иначе товар из одной позиции скорился бы против требований ВСЕГО лота (чужие
        # позиции → «пробелы» → заниженный %). Кэш «индекс позиции → требования»: на лот
        # обычно 1-2 позиции с товарами, извлечение не дублируется по каждому товару.
        pos_codes = [p.code for p in positions]
        reqs_by_position = {}

        def reqs_for(product_id):
            idx = best_position(codes_by_id.get(product_id, []), pos_codes) if len(positions) > 1 else None
            if idx not in reqs_by_position:
                pos = {"name": positions[idx].name,
                       "others": [p.name for j, p in enumerate(positions) if j != idx]} if idx is not None else None
                reqs_by_position[idx] = extract_requirements(tz_text, profile=profile, position=pos)
            return reqs_by_position[idx]

        # Шаг 7-8: сопоставить с каждым товаром (требования — уже по его позиции).
        for product in products:
            try:
                raw_reqs = reqs_for(product.id)
                req_fields = {r["key"]: r.get("value") for r in raw_reqs}
                # Семантический слой (Haiku, keymatch.py): маппинг имён ключей + сверка значений.
                mapping = align_keys(req_fields, {a.key: a.value for a in product.attributes},
                                     key_synonyms=profile.get("key_synonyms"))
                aligned = align_values(apply_mapping(raw_reqs, mapping, profile.get("critical_attributes")), product)
                reqs = to_requirements(aligned)
                res = match(product, reqs, purchase.id, profile.get("synonyms"))
            except Exception as e:                       # устойчивость к сбою LLM (§резильентность)
                msg = str(e).lower()
                if "usage limit" in msg or "rate limit" in msg or "429" in msg or "quota" in msg:
                    print("    ▶ достигнут лимит API Anthropic — останавливаю прогон "
                          "(собранные вердикты сохранены)")
                    source.close()
                    return
                print("    ⚠ %s пропущен: %s" % (product.id, type(e).__name__))
                continue
            # Сборный лот: где именно в нём товар и что ещё в лоте (§11.4).
            lot = lot_placement(product.id, codes_by_id, positions)
            lot_str = ("  [позиция %d из %d]" % (lot["position"], lot["total"])) if lot else ""
            print("    %-28s %3d%%  %s%s" % (product.id, res.score, VERDICT_RU[res.verdict], lot_str))
            out = os.path.join(args.out, "%s__%s.json" % (purchase.id, product.id))
            with open(out, "w", encoding="utf-8") as f:
                json.dump({
                    "purchase_id": purchase.id, "subject": purchase.subject,
                    "product_id": product.id, "score": res.score,
                    "verdict": res.verdict.value, "explanation": res.explanation,
                    # практичная обвязка закупки для ленты (карточка контракта, §Этап 1)
                    "customer": purchase.customer, "price": purchase.price,
                    "region": purchase.region, "submission_close": purchase.submission_close,
                    "url": "https://tenderplan.ru/app?tender=%s" % purchase.id,
                    "lot": lot,
                    "checks": [{"req": c.req.key, "status": c.status.value,
                                "note": c.note, "action": c.action} for c in res.checks],
                    # для дозаполнения пробелов клиентом: замороженные требования + синонимы
                    # профиля → API пересчитывает % без повторного извлечения (§Этап 1)
                    "requirements": aligned,
                    "synonyms": profile.get("synonyms") or {},
                }, f, ensure_ascii=False, indent=2)

    source.close()
    print("\nГотово. Обработано закупок: %d (отсеяно по сроку: %d, по региону: %d, по КТРУ: %d). "
          "Вердикты в %s/" %
          (processed, skipped_deadline, skipped_region, skipped_ktru, args.out))


if __name__ == "__main__":
    main()
