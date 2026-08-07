"""Грубый фильтр воронки (шаг 2, plan.md §3) — детерминированный код, БЕЗ LLM.

По профилю категории (коды ОКПД2/КТРУ + ключевые слова/синонимы) отсеивает явно
нерелевантные закупки ДО дорогого семантического отсева и LLM-разбора. Дешёвый
верх воронки: сотни тыс. закупок/день → сотни кандидатов на поставщика.

Совпадение по коду классификатора — сильный сигнал; по ключевым словам — слабый.
Возвращаем закупки, прошедшие порог, с оценкой релевантности для сортировки.
"""
from __future__ import annotations

import re
import time
from typing import Iterable, List, Optional, Tuple

from schema import Purchase

# Веса сигналов: точный код классификатора надёжнее текстового совпадения.
_W_CODE = 10.0
_W_KEYWORD = 1.0


def _norm(s: str) -> str:
    return s.lower().replace("ё", "е")


def _code_prefix_match(codes: List[str], profile_codes: List[str]) -> bool:
    """Код закупки подходит, если начинается с кода из профиля (22.19 ⊂ 22.19.60.119)."""
    for c in codes:
        cn = c.strip()
        for pc in profile_codes:
            pcn = pc.strip()
            if cn.startswith(pcn) or pcn.startswith(cn):
                return True
    return False


def _keyword_hits(text: str, keywords: List[str]) -> int:
    """Сколько ключевых слов профиля встретилось в тексте (совпадение от начала слова).

    Используем ТОЛЬКО profile.keywords — это курируемые различительные основы
    («перчат», «нитрил», «латекс»). Синонимы профиля сюда НЕ подмешиваем: они про
    семантику значений для matcher.py, а не про discovery — общий синоним вроде «без»
    даёт ложные срабатывания («без» ∈ «безопасности»).
    """
    terms = {_norm(k) for k in keywords if k}
    t = _norm(text)
    return sum(1 for term in terms if re.search(r"\b" + re.escape(term), t))


def score_purchase(purchase: Purchase, profile: dict) -> float:
    """Оценка релевантности закупки профилю категории. 0 — не релевантна.

    profile.stop_keywords — стоп-слова: если встретилось хоть одно, закупка НЕ релевантна
    (0), даже при совпадении кода/ключей. Нужно для омонимов discovery: «амбу»∈«амбулаторный»,
    «искусственной вентиляции» ловит дыхательные контуры/влагообменники (не мешок Амбу)."""
    if _keyword_hits(purchase.subject, profile.get("stop_keywords", [])):
        return 0.0
    profile_codes = profile.get("okpd2_ktru", [])
    code_match = _code_prefix_match(purchase.okpd2 + purchase.ktru, profile_codes)
    hits = _keyword_hits(purchase.subject, profile.get("keywords", []))
    return (_W_CODE if code_match else 0.0) + _W_KEYWORD * hits


def in_region(purchase: Purchase, regions: Optional[Iterable]) -> bool:
    """Регион закупки входит в набор. regions=None/пусто → вся РФ (пропускаем всё)."""
    if not regions:
        return True
    return str(purchase.region) in {str(r) for r in regions}


def days_to_deadline(purchase: Purchase, now_ms: Optional[int] = None) -> Optional[float]:
    """Сколько дней до окончания подачи заявок. None, если даты нет."""
    if not purchase.submission_close:
        return None
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    return (purchase.submission_close - now_ms) / 86_400_000.0


def deadline_in_window(purchase: Purchase, min_days: float = 1, max_days: float = 29,
                       now_ms: Optional[int] = None) -> bool:
    """Дедлайн подачи в окне [min_days, max_days] дней от сейчас.

    Отсекает уже закрытые (< min_days) и слишком дальние (> max_days). Нет даты → False
    (не подтверждено, что закупка открыта — не показываем в «активных»).
    """
    d = days_to_deadline(purchase, now_ms)
    return d is not None and min_days <= d <= max_days


def coarse_filter(purchases: List[Purchase], profile: dict,
                  min_score: float = 1.0) -> List[Tuple[Purchase, float]]:
    """Отсеять нерелевантные закупки, отсортировать по убыванию релевантности.

    min_score=1.0 — хотя бы одно ключевое слово ИЛИ совпадение кода. Порог грубый
    намеренно: задача шага 2 — не потерять кандидата, точный отсев дальше (шаги 3, 6).
    """
    scored = [(p, score_purchase(p, profile)) for p in purchases]
    passed = [(p, s) for p, s in scored if s >= min_score]
    passed.sort(key=lambda ps: ps[1], reverse=True)
    return passed
