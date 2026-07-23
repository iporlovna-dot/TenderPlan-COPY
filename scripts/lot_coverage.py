"""Покрытие лота каталогом: «сколько позиций закупки — наши» (сборная солянка).

Реализует сценарий вкладки «Товары»: приходит многолот (напр. 8 клинков разного
типа/размера) → сервис показывает, какие позиции закрываются нашим складом и чем.

Как работает:
  1. Позиции лота берём из Тендерплана (fullinfo).
  2. Характеристики КАЖДОЙ позиции парсим из таблицы ТЗ ДЕТЕРМИНИРОВАННО (structured
     «Ключ: Значение. Ключ: Значение.» из ячеек pipe-таблицы) — LLM не нужен, это и есть
     правильный источник требований (см. plan.md §6). Разбор по КТРУ движок сделать не может
     (в многолоте позиции часто с ОДНИМ КТРУ и одним именем — спека только в таблице ТЗ).
  3. Каждую позицию матчим против каталога: КТРУ-предфильтр (не гонять чужие категории) →
     семантика (align_keys/align_values) → matcher. Лучший товар = покрытие позиции.
  4. Сводка: N из M позиций закрыты, чем именно.

Запуск:
    .venv/bin/python scripts/lot_coverage.py --id <tender_id> \
        --catalog data/products/ --profile data/profiles/laryngoscope.json

Порог «покрыто» — --min-score (по умолчанию 60). Ключи в окружении (TENDERPLAN_TOKEN,
ANTHROPIC_API_KEY). ТЗ на zakupki.mos.ru пока не качается штатно (SSL, техдолг).
"""
import argparse
import glob
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from schema import Attribute, Hardness, Operator, Product, ReqType, Requirement, Verdict  # noqa: E402
from parser import parse  # noqa: E402
from keymatch import align_keys, align_values, apply_mapping  # noqa: E402
from matcher import match  # noqa: E402
from ktru import ktru_relation  # noqa: E402
from tenderplan import TenderplanSource  # noqa: E402

PARSE_EXT = (".docx", ".pdf", ".xlsx", ".txt")
TZ_HINTS = ("описание объекта", "техническ", "задание", "характеристик")
VR = {Verdict.ELIGIBLE: "✓", Verdict.ELIGIBLE_WITH_GAPS: "✓~", Verdict.DISQUALIFIED: "✗"}


def _kv_pairs(cell: str) -> dict:
    """«Материал: Сталь. Тип клинка: Миллер. Размер: 00.» → {материал:'Сталь', ...}.

    Характеристики позиции ЕИС — структурные пары. Делим по «. »/«; », ключ до первого
    «:». Ключ нормализуем в snake_case (нижний регистр, пробелы→_)."""
    out = {}
    for seg in re.split(r"[.;]\s+", cell):
        if ":" in seg:
            k, v = seg.split(":", 1)
            k = re.sub(r"\s+", "_", k.strip().lower())
            v = v.strip().rstrip(".").strip()
            if k and v and len(k) <= 40:
                out[k] = v
    return out


def parse_position_specs(tz_text: str) -> list:
    """Из pipe-таблицы ТЗ достаём характеристики позиций: ячейки с ≥3 парами «Ключ: Значение».

    Возвращает список dict-ов (по позиции). Дедуп подряд идущих одинаковых строк
    (в pdf одна позиция иногда двоится)."""
    specs = []
    for line in tz_text.splitlines():
        if not line.startswith("|"):
            continue
        cell0 = line.strip("|").split("|")[0].strip()
        pairs = _kv_pairs(cell0)
        if len(pairs) >= 3:
            if not specs or specs[-1] != pairs:
                specs.append(pairs)
    return specs


def load_catalog(path):
    files = [path] if path.endswith(".json") else glob.glob(os.path.join(path, "*.json"))
    cat = []
    for fp in files:
        d = json.load(open(fp, encoding="utf-8"))
        cat.append((d, Product(id=d["id"], name=d["name"],
                                attributes=[Attribute(**a) for a in d["attributes"]])))
    return cat


def best_card(spec, pos_code, catalog, synonyms, critical):
    """Лучший товар каталога под позицию: КТРУ-предфильтр (не 'none') → align → match.

    Критичные поля профиля (`тип_клинка`, `одноразовость`…) делаем ЖЁСТКИМИ: расхождение
    по ним → дисквалификация, товар не считается покрытием (Миллер vs Макинтош → не наш,
    а не «57%»). Ранжируем: сначала не-дисквалифицированные, потом по %."""
    crit = set(critical or [])
    reqs_raw = [{"key": k, "value": v} for k, v in spec.items()]
    best = None
    for d, product in catalog:
        codes = d.get("ktru") or []
        if pos_code and codes and ktru_relation(codes, [pos_code]) == "none":
            continue  # чужая категория — не тратим align
        mapping = align_keys(dict(spec), {a.key: a.value for a in product.attributes})
        aligned = align_values(apply_mapping(reqs_raw, mapping), product)
        reqs = [Requirement(key=r["key"], operator=Operator.EQ, value=r.get("value"), unit="",
                            hardness=(Hardness.HARD if r["key"] in crit else Hardness.SOFT),
                            type=ReqType.TECHNICAL, raw="%s: %s" % (r["key"], r.get("value")))
                for r in aligned]
        res = match(product, reqs, "cov", synonyms)
        rank = (res.verdict != Verdict.DISQUALIFIED, res.score)  # не-дисквал важнее %
        if best is None or rank > best[0]:
            best = (rank, product.id, res.score, res.verdict)
    return (best[1], best[2], best[3]) if best else None


def main():
    ap = argparse.ArgumentParser(description="Покрытие лота каталогом")
    ap.add_argument("--id", required=True, help="id тендера Тендерплана")
    ap.add_argument("--catalog", required=True, help="каталог карточек (папка или .json)")
    ap.add_argument("--profile", help="профиль категории (для синонимов)")
    ap.add_argument("--min-score", type=float, default=60, help="порог «покрыто», %")
    args = ap.parse_args()

    profile = json.load(open(args.profile, encoding="utf-8")) if args.profile else {}
    synonyms = profile.get("synonyms")
    critical = profile.get("critical_attributes")
    catalog = load_catalog(args.catalog)

    src = TenderplanSource()
    try:
        purchase = src.get_tender(args.id)
        positions = src.positions(args.id)
        with tempfile.TemporaryDirectory() as tmp:
            files = src.download_attachments(purchase, tmp)
            tzf = [f for f in files if f.lower().endswith(PARSE_EXT)
                   and any(h in os.path.basename(f).lower() for h in TZ_HINTS)] \
                or [f for f in files if f.lower().endswith(PARSE_EXT)]
            tz = "\n\n".join(parse(f) for f in tzf)
    finally:
        src.close()

    specs = parse_position_specs(tz)
    print("Лот: %s" % (purchase.subject or "")[:80])
    print("Позиций в закупке: %d | характеристик распознано из ТЗ: %d | каталог: %d\n"
          % (len(positions), len(specs), len(catalog)))

    pos_codes = [p.code for p in positions]
    covered = 0
    for i, spec in enumerate(specs):
        code = pos_codes[i] if i < len(pos_codes) else (pos_codes[0] if pos_codes else None)
        label = ", ".join("%s: %s" % (k, v) for k, v in spec.items())
        best = best_card(spec, code, catalog, synonyms, critical)
        print("  [%d] %s" % (i + 1, label))
        if best and best[2] != Verdict.DISQUALIFIED and best[1] >= args.min_score:
            covered += 1
            print("       → %s НАШ: %s (%d%%)" % (VR[best[2]], best[1] and best[0], best[1]))
        elif best and best[2] == Verdict.DISQUALIFIED:
            print("       → ✗ не наш (несовпадение по критичному полю; ближе всего %s)" % best[0])
        elif best:
            print("       → ✗ ниже порога (ближе всего %s, %d%%)" % (best[0], best[1]))
        else:
            print("       → ✗ нет в каталоге (чужой КТРУ)")

    print("\n▶ ПОКРЫТИЕ: %d из %d позиций — наш каталог (порог %g%%)" % (covered, len(specs), args.min_score))


if __name__ == "__main__":
    main()
