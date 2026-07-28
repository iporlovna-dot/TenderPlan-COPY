"""Извлечение срока исполнения контракта и этапов из проекта контракта (plan.md §Этап 1).

Источник (Тендерплан/ЕИС) НЕ отдаёт срок исполнения отдельным полем — он живёт в тексте проекта
контракта (вложение). Здесь — ДЕТЕРМИНИРОВАННЫЙ best-effort парсер по типовым формулировкам РФ:
«в течение 30 календарных дней с даты заключения», «не позднее 31.12.2026», «поэтапно: Этап 1 …».
LLM не участвует (квота-независимо); апгрейд до LLM — когда откроется квота, интерфейс тот же.

Результат — {execution_period, stages[], confidence}. confidence честно помечает надёжность: `high`
(нашли по явному триггеру/этапам), `medium` (значение без триггера), `none` (не нашли — не выдумываем).
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

# дата: 31.12.2026 | 31/12/26 | 31 декабря 2026
_DATE_NUM = r"\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}"
_DATE_WORD = r"\d{1,2}\s+[а-яё]{3,9}\s+\d{4}(?:\s*(?:года|г\.?))?"
_DATE = r"(?:%s|%s)" % (_DATE_NUM, _DATE_WORD)
# длительность: 30 (тридцати) календарных дней | 15 рабочих дней | 3 месяца | 6 недель
_DUR = r"\d+\s*(?:\([^)]*\)\s*)?(?:календарн\w+|рабоч\w+)?\s*(?:дн\w+|месяц\w*|недел\w+|год\w*)"

_UNTIL = r"(?:не\s+позднее|до|по)\s+%s" % _DATE
_WITHIN = r"(?:в\s+течение|в\s+срок(?:\s+не\s+более)?)\s+%s" % _DUR

_EXEC_TRIGGERS = (
    "срок исполнения контракт", "срок исполнения договор", "срок исполнения обязательств",
    "срок поставки", "срок выполнения работ", "срок оказания услуг",
    "поставка товара осуществляется", "товар поставляется", "срок передачи товара",
)

_VAL_PATTERNS = (_WITHIN, _UNTIL, _DUR, _DATE)


def _first_value(window: str) -> Optional[str]:
    for p in _VAL_PATTERNS:
        m = re.search(p, window, re.I)
        if m:
            return re.sub(r"\s+", " ", m.group(0)).strip()
    return None


def _stages(text: str) -> List[str]:
    """Этапы: «Этап 1 — до 01.06.2026; Этап 2 …» → ['Этап 1: до 01.06.2026', …] (по порядку, без дублей).

    Окно берём фиксированной ширины вперёд от «Этап N» (не по «.», т.к. точки есть внутри дат)."""
    out, seen = [], set()
    for m in re.finditer(r"этап\s*(?:№\s*)?(\d+)", text, re.I):
        num = m.group(1)
        if num in seen:
            continue
        val = _first_value(text[m.start():m.start() + 90])   # окно вперёд, дата с точками цела
        if val:
            seen.add(num)
            out.append("Этап %s: %s" % (num, val))
    return out


def parse_contract_terms(text: str) -> Dict[str, object]:
    """Текст проекта контракта → {execution_period, stages, confidence}. Пусто → confidence 'none'."""
    if not text or not text.strip():
        return {"execution_period": None, "stages": [], "confidence": "none"}
    t = re.sub(r"[ \t]+", " ", text)
    low = t.lower()

    execution = None
    for trig in _EXEC_TRIGGERS:
        i = low.find(trig)
        if i != -1:
            execution = _first_value(t[i:i + 240])
            if execution:
                break

    stages = _stages(t)

    if execution or stages:
        confidence = "high"
    else:
        # запасной путь: значение рядом со словом «контракт»/«поставк» без явного триггера
        m = re.search(r"(?:контракт\w*|поставк\w*)[^.;\n]{0,120}?(%s|%s)" % (_WITHIN, _UNTIL), low, re.I)
        if m:
            execution = re.sub(r"\s+", " ", m.group(1)).strip()
            confidence = "medium"
        else:
            confidence = "none"

    return {"execution_period": execution, "stages": stages, "confidence": confidence}


def summary(res: Dict[str, object]) -> Optional[str]:
    """Единая читаемая строка для карточки контракта (`ContractCard.execution_period`)."""
    parts = []
    if res.get("execution_period"):
        parts.append("Срок исполнения: %s" % res["execution_period"])
    if res.get("stages"):
        parts.append("Этапы — %s" % "; ".join(res["stages"]))
    return " | ".join(parts) or None
