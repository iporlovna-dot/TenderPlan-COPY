"""Сверка карточек товара с ПОЗИЦИЯМИ закупки — через настоящий движок matcher/.

Что это и зачем. До сих пор в проде жил только `app/matching.py` — сверка
текст↔текст: мешок терминов моего ТЗ против мешка терминов закупки. Она отвечает
на «это вообще про мой товар?», но не на «а по параметрам проходим?», потому что
у неё нет ни операторов, ни единиц, ни понятия дисквалификации.

Теперь у нас есть обе стороны настоящей сверки:
  • требования — `lotItems[].chars` из таблицы КТРУ (tools/sources/ktrutable.js),
    уже с операторами gte/lte/range/eq/present и жёсткостью hard/soft; там, где
    структурированной таблицы нет (см. spec_llm.py — 87%/98% позиций 44/223-ФЗ),
    требования лениво извлекает LLM из названия позиции, только когда для неё
    уже выбран товар пользователя ниже;
  • карточки товара — `Product` с атрибутами, из кабинета пользователя.
Ядро `matcher.match()` умеет их сравнивать и покрыто 39 тестами. Здесь — только
мост: прочитать позиции, перевести словарь, выбрать товар под позицию, собрать
ответ. Никакой логики сравнения тут быть не должно.

⚠️ Источники этому модулю НЕ нужны. Бэкенд «Лекало» отложили, потому что VPS
заблокирован zakupki.gov.ru и mos.ru, — но сверка считает по уже собранным
данным, лежащим на диске рядом (site/data/spec.json, доставляется refresh.sh).
Причина отказа от бэкенда к этой задаче не относится.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import sys
import threading
from typing import Dict, List, Optional, Tuple

# Ядро живёт отдельным проектом, влитым сюда через git subtree, и зависимостей не
# требует вообще — достаточно положить его src на путь импорта.
_MATCHER_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "matcher", "src")
if _MATCHER_SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_MATCHER_SRC))

import keymatch  # noqa: E402
from ktru import ktru_relation, EXACT, GROUP, NONE  # noqa: E402
from matcher import match as engine_match  # noqa: E402
from schema import (  # noqa: E402
    Attribute, Hardness, MatchResult, Operator, Product, Requirement, Verdict,
)

from . import spec_llm  # noqa: E402

# Детерминированный слой align_keys (морфология имени: регистр/ё-е/разделители) — бесплатный,
# без сети, поэтому включён по умолчанию. LLM/эмбеддинг-добор остатка сюда сознательно НЕ
# подключён (см. plan.md фазы 2-3) — llm_fallback=False ниже гарантирует, что вызов не создаст
# anthropic-клиента и не потратит деньги.
ALIGN_KEYS = os.getenv("LK_ALIGN_KEYS", "1") != "0"

# Файл довеска со спецификацией — тот же, что грузит фронт при раскрытии
# карточки. Отдельной копии данных для бэкенда не заводим: разъехавшиеся копии
# врали бы по-разному.
SPEC_FILE = os.getenv(
    "LK_SPEC_FILE",
    os.path.join(os.path.dirname(__file__), "..", "..", "site", "data", "spec.json"),
)

_OPERATORS = {
    "gte": Operator.GTE,
    "lte": Operator.LTE,
    "range": Operator.RANGE,
    "eq": Operator.EQ,
    "present": Operator.PRESENT,
}
_HARDNESS = {"hard": Hardness.HARD, "soft": Hardness.SOFT}


# ─────────────────────────────────────────────────────── чтение позиций закупки

_cache: dict = {"mtime": None, "spec": {}}
_lock = threading.Lock()


def _load_spec() -> Dict[str, list]:
    """Позиции всех закупок из spec.json. Перечитываем по времени изменения
    файла: refresh.sh кладёт новый каждый час, а держать процесс на устаревших
    данных — молча показывать вчерашние требования."""
    try:
        mtime = os.path.getmtime(SPEC_FILE)
    except OSError:
        return {}
    with _lock:
        if _cache["mtime"] == mtime:
            return _cache["spec"]
        try:
            with open(SPEC_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
            _cache["spec"] = data.get("spec") or {}
            _cache["mtime"] = mtime
        except (OSError, ValueError):
            # битый/недописанный файл — отдаём прежние данные, а не пустоту:
            # refresh.sh пишет через .tmp + mv, но подстраховаться дёшево
            pass
        return _cache["spec"]


def positions(purchase_id: str) -> List[dict]:
    """Позиции лота закупки (пусто, если спецификации нет)."""
    return _load_spec().get(purchase_id) or []


# ─────────────────────────────────────────────────────────── перевод словаря

def to_requirements(item: dict) -> List[Requirement]:
    """Характеристики позиции → требования движка.

    Словарь совпадает по построению (ktrutable.js писался под schema.py), но
    неизвестный оператор молча пропускаем, а не подставляем EQ: выдуманное
    требование хуже отсутствующего — оно даст ложное нарушение."""
    out: List[Requirement] = []
    for ch in item.get("chars") or []:
        op = _OPERATORS.get(ch.get("operator"))
        if op is None:
            continue
        out.append(Requirement(
            key=ch.get("key") or "",
            operator=op,
            value=ch.get("value"),
            unit=ch.get("unit") or None,
            hardness=_HARDNESS.get(ch.get("hardness"), Hardness.SOFT),
            raw=ch.get("raw") or "",
        ))
    return out


def to_product(d: dict) -> Product:
    """Карточка товара из кабинета → Product движка."""
    attrs = []
    for a in d.get("attributes") or []:
        attrs.append(Attribute(
            key=a.get("key") or "",
            value=a.get("value"),
            status=a.get("status") or "declared",
            doc=a.get("doc"),
        ))
    return Product(id=d.get("id") or "product", name=d.get("name") or "", attributes=attrs)


def _align_keys(reqs: List[Requirement], product_attrs: List[dict]) -> List[Requirement]:
    """Свести ключи требований к ключам карточки там, где расходится только написание
    (регистр/ё-е/разделители/порядок слов) — «Толщина, мм» ТЗ и «толщина» карточки иначе
    сравниваются буквально и дают ложный пробел вместо реальной проверки.

    Только детерминированный слой `keymatch.align_keys` (`llm_fallback=False`) — без сети и
    без денег; LLM/эмбеддинг-добор остатка сознательно не подключён (см. plan.md фазы 2-3)."""
    if not ALIGN_KEYS or not reqs or not product_attrs:
        return reqs
    req_fields = {r.key: r.value for r in reqs if r.key}
    product_fields = {a.get("key"): a.get("value") for a in product_attrs if a.get("key")}
    mapping = keymatch.align_keys(req_fields, product_fields, llm_fallback=False)
    if not mapping:
        return reqs
    return [
        dataclasses.replace(r, key=mapping[r.key], remapped=True) if r.key in mapping else r
        for r in reqs
    ]


# ──────────────────────────────────────────────── какой товар к какой позиции

_WORD = re.compile(r"[а-яёa-z0-9]{3,}", re.IGNORECASE)


def _tokens(s: str) -> set:
    return {w.lower() for w in _WORD.findall(s or "")}


def _name_overlap(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def pick_product(item: dict, products: List[dict]) -> Tuple[Optional[dict], str]:
    """Товар, который вообще может закрыть эту позицию, и по какому признаку.

    Код КТРУ решает первым: он федеральный и однозначный, а название заказчик
    пишет как хочет. `exact` — та же позиция каталога, `group` — та же товарная
    группа. Только если кодов нет ни с одной стороны, падаем на пересечение слов.
    """
    pos_codes = [c for c in (item.get("ktru"), item.get("okpd")) if c]
    best, best_reason, best_score = None, "none", 0.0
    for p in products:
        rel = ktru_relation(p.get("ktru") or [], pos_codes) if pos_codes else NONE
        if rel == EXACT:
            return p, EXACT
        score = _name_overlap(p.get("name", ""), item.get("name", ""))
        if rel == GROUP:
            score += 1.0            # группа важнее любого совпадения слов
        reason = GROUP if rel == GROUP else ("name" if score > 0 else "none")
        if score > best_score:
            best, best_reason, best_score = p, reason, score
    return best, best_reason


# ───────────────────────────────────────────────────────────────── сверка лота

# Лот берут целиком: закрыть 3 позиции из 24 — это отказ, а не 12%. Поэтому
# считаем по позициям и отдельно говорим, сколько из них закрыто.
def match_lot(purchase_id: str, products: List[dict]) -> dict:
    items = positions(purchase_id)
    if not items:
        return {"status": "no-spec", "purchase_id": purchase_id,
                "positions": [], "covered": 0, "total": 0}

    prods = list(products or [])
    results = []
    covered = 0
    unverified = 0
    for idx, item in enumerate(items):
        reqs = to_requirements(item)
        # Кандидат товара решаем ДО обращения к LLM: без товара сверять
        # позицию всё равно нечем, а извлечение требований стоит денег —
        # тратить его на позицию без единого подходящего товара в кабинете
        # бессмысленно (см. spec_llm.py: объём должен зависеть от реального
        # интереса пользователя, не от размера корпуса).
        chosen, reason = pick_product(item, prods)
        if chosen is None:
            results.append({
                "index": idx, "name": item.get("name", ""), "ktru": item.get("ktru", ""),
                "qty": item.get("qty"), "unit": item.get("unit", ""),
                "product_id": None, "match_by": "none",
                "verdict": Verdict.DISQUALIFIED.value, "score": 0,
                "checks": [], "requirements": len(reqs),
                "note": "в каталоге нет подходящего товара",
            })
            continue
        # Структурированной таблицы нет (goods-фолбэк ktrutable.js) — пробуем
        # вытащить требования LLM'ом из названия позиции (см. spec_llm.py):
        # заказчик часто пишет характеристики прямо в ячейке товара текстом.
        if not reqs and spec_llm.should_try(item.get("name", "")):
            llm_chars = spec_llm.extract_cached(item.get("name", ""))
            if llm_chars:
                reqs = to_requirements({"chars": llm_chars})
        # ⚠️ Всё равно пусто — не «сверять нечего, значит проходит» (движок сам
        # теперь честен и на пустом checks даёт ELIGIBLE_WITH_GAPS, см.
        # matcher.py), а отдельный статус здесь, в мосте: позицию нельзя ни
        # засчитать в `covered` (мы ничего не проверили), ни списать в провал
        # всего лота (это дыра в данных, а не нарушение требования).
        if not reqs:
            unverified += 1
            results.append({
                "index": idx, "name": item.get("name", ""), "ktru": item.get("ktru", ""),
                "qty": item.get("qty"), "unit": item.get("unit", ""),
                "product_id": chosen.get("id"), "product_name": chosen.get("name", ""),
                "match_by": reason,
                "verdict": "unverified", "score": 0,
                "checks": [], "requirements": 0,
                "note": "в документе закупки нет структурированных характеристик для этой позиции — сверить нечем",
            })
            continue
        reqs = _align_keys(reqs, chosen.get("attributes") or [])
        res: MatchResult = engine_match(to_product(chosen), reqs, purchase_id)
        ok = res.verdict != Verdict.DISQUALIFIED
        if ok:
            covered += 1
        results.append({
            "index": idx, "name": item.get("name", ""), "ktru": item.get("ktru", ""),
            "qty": item.get("qty"), "unit": item.get("unit", ""),
            "product_id": chosen.get("id"), "product_name": chosen.get("name", ""),
            "match_by": reason,
            "verdict": res.verdict.value, "score": res.score,
            "requirements": len(reqs),
            "checks": [{
                "key": c.req.key,
                # ⚠️ У фронта Лекало исторически `fail`, у движка — `violation`.
                # Переводим ЗДЕСЬ, одним местом, а не в каждом шаблоне.
                "status": "fail" if c.status.value == "violation" else c.status.value,
                "hard": c.req.hardness == Hardness.HARD,
                "expected": c.req.raw or _describe(c.req),
                # note/action пишет сам движок: что не так и что с этим делать
                # поставщику. Пересказывать своими словами нельзя — разъедется.
                "note": c.note or "",
                "action": c.action or "",
            } for c in res.checks],
        })

    # Лот всё-или-ничего, но «не смогли проверить» и «проверили и не прошли» —
    # разные вещи, и раньше первое молча тонуло во втором (covered < total →
    # DISQUALIFIED целиком, даже если ни одного реального нарушения не было).
    # Теперь: есть настоящий провал (нашли продукт, но требование нарушено) →
    # дисквалификация как и раньше; иначе, если что-то осталось непроверенным
    # (unverified) или без подходящего товара — «есть пробелы», не «не проходит».
    failed = len(items) - covered - unverified
    if failed > 0:
        verdict = Verdict.DISQUALIFIED.value
    elif covered == len(items):
        verdict = Verdict.ELIGIBLE.value
    else:
        verdict = Verdict.ELIGIBLE_WITH_GAPS.value

    return {
        "status": "ok", "purchase_id": purchase_id,
        "positions": results, "covered": covered, "total": len(items),
        "verdict": verdict,
    }


def _describe(r: Requirement) -> str:
    unit = f" {r.unit}" if r.unit else ""
    if r.operator == Operator.GTE:
        return f"≥ {r.value}{unit}"
    if r.operator == Operator.LTE:
        return f"≤ {r.value}{unit}"
    if r.operator == Operator.RANGE and isinstance(r.value, (list, tuple)) and len(r.value) == 2:
        return f"{r.value[0]}–{r.value[1]}{unit}"
    if r.operator == Operator.PRESENT:
        return "требуется"
    return f"{r.value}{unit}"
