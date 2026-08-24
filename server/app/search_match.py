"""Матчинг закупки под сохранённый поиск — серверный порт логики фронта
(site/js/appview.js: tokens/stem/fleetingVowelVariant/passesSearch/passesFilters/
liveStage). Нужен ежедневному дайджесту (scripts/notify_new_purchases.py): скрипт
не может звать браузерный код, а расхождение алгоритмов дало бы уведомления не про
то, что человек видит в ленте.

⚠️ Держать в синхроне с appview.js. Любая правка отбора там (stem-окончания,
беглая гласная, поля haystack) должна повторяться здесь, иначе дайджест разъедется
с лентой.
"""

from __future__ import annotations

import math
import re
from datetime import datetime

# --- стемминг (зеркало appview.js) ---

RU_ENDINGS = [
    "иями", "иях",
    "ами", "ями", "его", "ому", "ыми", "ими", "ого",
    "ах", "ях", "ов", "ев", "ий", "ый", "их", "ых", "ая", "яя", "ое", "ые", "ие", "ом", "ем", "им", "ым", "ой", "ей",
    "а", "я", "о", "е", "и", "ы", "у", "ю", "й", "ь",
]
STEM_MIN = 4

_TOKEN_RE = re.compile(r"[а-яёa-z0-9]+")
_FV_RE = re.compile(r"^(.+[бвгджзйклмнпрстфхцчшщ])[оеё]к$")


def tokens(s: str) -> list[str]:
    return _TOKEN_RE.findall((s or "").lower())


def stem(word: str) -> str:
    w = (word or "").lower()
    if len(w) <= STEM_MIN:
        return w
    for suf in RU_ENDINGS:
        if len(w) - len(suf) >= STEM_MIN and w.endswith(suf):
            return w[: len(w) - len(suf)]
    return w


def fleeting_vowel_variant(word: str):
    m = _FV_RE.match(word or "")
    return (m.group(1) + "к") if m else None


def _word_matches(w: str, hay_words: list[str]) -> bool:
    s = stem(w)
    s_alt = fleeting_vowel_variant(s)
    for hw in hay_words:
        if hw.startswith(s):
            return True
        if s_alt and hw.startswith(s_alt):
            return True
        hw_alt = fleeting_vowel_variant(hw)
        if hw_alt and hw_alt.startswith(s):
            return True
    return False


def purchase_haystack(p: dict) -> str:
    lot_names = " ".join(str(l.get("name") or "") for l in (p.get("lots") or []))
    return (str(p.get("title") or "") + " " + lot_names + " " + str(p.get("number") or "")).lower()


def passes_search(p: dict, query: str, minus: str) -> bool:
    hay_words = tokens(purchase_haystack(p))
    plus = tokens(query or "")
    minus_t = tokens(minus or "")
    if any(_word_matches(w, hay_words) for w in minus_t):
        return False
    if plus and not all(_word_matches(w, hay_words) for w in plus):
        return False
    return True


# --- живой этап / срок (зеркало appview.js liveStage/daysLeft/isExpired) ---

def _parse_dt(s: str):
    try:
        return datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None


def is_expired(p: dict, now: datetime) -> bool:
    ed = p.get("endDate")
    if not ed:
        return False
    dt = _parse_dt(ed)
    if dt is None:
        return False
    return dt <= _align(now, dt)


def _align(now: datetime, other: datetime) -> datetime:
    """endDate из снапшота обычно наивный ('2026-08-24T10:22:11'). Сравниваем в
    одной «зоне»: если у even нет tz — снимаем tz и у now, иначе оставляем aware."""
    if other.tzinfo is None:
        return now.replace(tzinfo=None)
    return now


def days_left(p: dict, now: datetime) -> int:
    ed = p.get("endDate")
    if ed:
        dt = _parse_dt(ed)
        if dt is not None:
            secs = (dt - _align(now, dt)).total_seconds()
            return math.ceil(secs / 86400)
    dd = p.get("deadlineDays")
    return dd if dd is not None else 0


def is_competitive(p: dict) -> bool:
    return p.get("competitive") is not False


def is_submittable(p: dict) -> bool:
    ps = p.get("procStage")
    return (not ps) or ps == "Подача заявок"


def live_stage(p: dict, now: datetime) -> str:
    if p.get("endDate"):
        return "completed" if is_expired(p, now) else "active"
    return p.get("stage")


def is_actionable(p: dict, now: datetime) -> bool:
    """Закупка, на которую ещё реально можно податься — только такие идут в
    дайджест (notDone на фронте: не истёкшая, конкурентная, приём заявок идёт)."""
    return (
        not is_expired(p, now)
        and is_competitive(p)
        and is_submittable(p)
        and live_stage(p, now) == "active"
    )


def passes_filters(p: dict, filters: dict, now: datetime) -> bool:
    # Сохранённый поиск (модалка «Новый поиск») задаёт из фильтров только закон и
    # срок подачи; регион='all', этап='active', цена 0/None — покрыто is_actionable.
    filters = filters or {}
    law = filters.get("law")
    if law and law != "all" and p.get("law") != law:
        return False
    wd = filters.get("windowDays")
    if wd is not None and wd < 999 and days_left(p, now) > wd:
        return False
    return True


def purchase_matches(p: dict, search: dict, now: datetime) -> bool:
    """search — dict с ключами query, minus, filters (как строка searches в БД,
    filters уже распарсен из JSON)."""
    if not is_actionable(p, now):
        return False
    if not passes_search(p, search.get("query", ""), search.get("minus", "")):
        return False
    if not passes_filters(p, search.get("filters") or {}, now):
        return False
    return True
