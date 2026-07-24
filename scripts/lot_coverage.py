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

PARSE_EXT = (".docx", ".doc", ".pdf", ".xlsx", ".xls", ".txt")
TZ_HINTS = ("описание объекта", "техническ", "задание", "характеристик")
VR = {Verdict.ELIGIBLE: "✓", Verdict.ELIGIBLE_WITH_GAPS: "✓~", Verdict.DISQUALIFIED: "✗"}


_KTRU_RE = re.compile(r"\d\d\.\d\d\.\d\d")


def _norm_key(s: str) -> str:
    return re.sub(r"\s+", "_", s.strip().lower())[:60]


def _op_value(raw: str):
    """Значение ЕИС → (operator, value). «≥ 60 и ≤ 65»→range[60,65]; «≥7.5»→gte 7.5;
    «≤1.5»→lte 1.5; «АВС пластик»→eq строка. Русскую запятую нормализуем."""
    s = raw.replace("\xa0", " ").strip().rstrip(".")
    low = s.lower()
    nums = re.findall(r"-?\d+(?:[.,]\d+)?", s)
    ge = "≥" in s or "не менее" in low or "не ранее" in low
    le = "≤" in s or "не более" in low or "не позднее" in low
    f = lambda x: float(x.replace(",", "."))  # noqa: E731
    if ge and le and len(nums) >= 2:
        return "range", [f(nums[0]), f(nums[1])]
    if ge and nums:
        return "gte", f(nums[0])
    if le and nums:
        return "lte", f(nums[0])
    return "eq", s


def _req(key, op, val):
    return {"key": key, "operator": op, "value": val, "unit": "",
            "hardness": "soft", "type": "technical", "raw": "%s: %s" % (key, val)}


def _parse_format_a(tz_text: str) -> list:
    """Формат A (все характеристики позиции в ОДНОЙ ячейке: «Материал: X. Тип: Y.»)."""
    out = []
    for line in tz_text.splitlines():
        if not line.startswith("|"):
            continue
        cell0 = line.strip("|").split("|")[0].strip()
        reqs = []
        for seg in re.split(r"[.;]\s+", cell0):
            if ":" in seg and len(seg) < 120:
                k, v = seg.split(":", 1)
                k, v = _norm_key(k), v.strip().rstrip(".")
                if k and v and len(k) <= 40:
                    op, val = _op_value(v)
                    reqs.append(_req(k, op, val))
        if len(reqs) >= 3:
            name = None
            pos = {"name": name, "reqs": reqs}
            if not out or [r["key"] for r in out[-1]["reqs"]] != [r["key"] for r in reqs]:
                out.append(pos)
    return out


def _parse_format_b(tz_text: str) -> list:
    """Формат B (по строке на характеристику: «| товар | наименование | КТРУ | Хар | Значение |»).

    Позиции группируем по наименованию; новая позиция — когда имя сменилось ИЛИ характеристика
    уже встречалась в текущей (в лоте несколько позиций с одним именем/КТРУ идут подряд)."""
    out = []
    cur = None
    for line in tz_text.splitlines():
        if not line.startswith("|"):
            continue
        c = [x.strip() for x in line.strip("|").split("|")]
        if len(c) < 5:
            continue
        name, ktru, char, val = c[1], c[2], c[3], c[4]
        if not _KTRU_RE.search(ktru) or not char or not val:
            continue
        key = _norm_key(char)
        keys_here = [r["key"] for r in cur["reqs"]] if cur else []
        if cur is None or name != cur["name"] or key in keys_here:
            cur = {"name": name, "reqs": []}
            out.append(cur)
        op, value = _op_value(val)
        cur["reqs"].append(_req(key, op, value))
    return [p for p in out if len(p["reqs"]) >= 2]


def parse_position_specs(tz_text: str) -> list:
    """Характеристики позиций из таблицы ТЗ. Поддержаны 2 формата ЕИС (A: всё в ячейке;
    B: по строке на характеристику). Возвращает [{name, reqs:[req-dict]}] по позиции."""
    return _parse_format_a(tz_text) or _parse_format_b(tz_text)


def _specs_ok(specs: list) -> bool:
    """Разбор годный? Пусто или ключи в основном числовые («1.1», «2.3» — нумерованные
    строки таблицы попали как ключи) → нет, нужен LLM-fallback."""
    keys = [r["key"] for s in specs for r in s["reqs"]]
    if not keys:
        return False
    numeric = sum(1 for k in keys if re.fullmatch(r"[\d.\s]+", k))
    return numeric / len(keys) < 0.5


def load_catalog(path):
    files = [path] if path.endswith(".json") else glob.glob(os.path.join(path, "*.json"))
    cat = []
    for fp in files:
        d = json.load(open(fp, encoding="utf-8"))
        cat.append((d, Product(id=d["id"], name=d["name"],
                                attributes=[Attribute(**a) for a in d["attributes"]])))
    return cat


def best_card(position_reqs, pos_code, catalog, synonyms, critical):
    """Лучший товар каталога под позицию: КТРУ-предфильтр (не 'none') → align → match.

    position_reqs — список req-dict {key,operator,value,...} характеристик позиции.
    Критичные поля профиля (`тип_клинка`, `одноразовость`…) делаем ЖЁСТКИМИ: расхождение
    по ним → дисквалификация, товар не считается покрытием (Миллер vs Макинтош → не наш,
    а не «57%»). Ранжируем: сначала не-дисквалифицированные, потом по %."""
    crit = set(critical or [])
    req_fields = {r["key"]: r.get("value") for r in position_reqs}
    best = None
    for d, product in catalog:
        codes = d.get("ktru") or []
        if pos_code and codes and ktru_relation(codes, [pos_code]) == "none":
            continue  # чужая категория — не тратим align
        mapping = align_keys(req_fields, {a.key: a.value for a in product.attributes})
        aligned = align_values(apply_mapping(position_reqs, mapping), product)
        reqs = [Requirement(key=r["key"], operator=Operator(r["operator"]), value=r.get("value"),
                            unit=r.get("unit"), type=ReqType.TECHNICAL,
                            hardness=(Hardness.HARD if r["key"] in crit else Hardness.SOFT),
                            raw=r.get("raw", "")) for r in aligned]
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
    ap.add_argument("--llm", action="store_true", help="принудительно LLM-разбор позиций (формат-независимо)")
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
    src_label = "таблица ТЗ (детерм.)"
    if tz.strip() and (args.llm or not _specs_ok(specs)):
        from extractor import extract_positions  # noqa: E402
        pos_ll = extract_positions(tz, profile=profile)
        specs = [{"name": p.get("name"), "reqs": p["requirements"]} for p in pos_ll]
        src_label = "LLM extract_positions (fallback)"

    print("Лот: %s" % (purchase.subject or "")[:80])
    print("Позиций в закупке: %d | распознано: %d [%s] | каталог: %d"
          % (len(positions), len(specs), src_label, len(catalog)))
    if not tz.strip():
        print("  ⚠ ТЗ не скачалось/пустое (вложение или SSL) — разбирать нечего")
    print()

    pos_codes = [p.code for p in positions]
    covered = 0
    for i, spec in enumerate(specs):
        code = pos_codes[i] if i < len(pos_codes) else (pos_codes[0] if pos_codes else None)
        label = (spec.get("name") + " — " if spec.get("name") else "") + \
            ", ".join("%s: %s" % (r["key"], r.get("value")) for r in spec["reqs"])
        best = best_card(spec["reqs"], code, catalog, synonyms, critical)
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
