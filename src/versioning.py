"""Версионность закупки: детект и КЛАССИФИКАЦИЯ изменения ТЗ после обработки (plan.md §6).

Домен (44-ФЗ): заказчик не может молча переписать ТЗ выпущенной процедуры — изменение оформляется
формальным «изменением извещения». Источник это фиксирует явно (revision-суффикс в id `_1/_2`,
`modification`, `isChanged`, `updateDateTime`), и срок подачи ПРОДЛЕВАЕТСЯ по закону. Значит:
  • основной сигнал изменения — ЯВНАЯ ревизия источника (updateDateTime новее), а не наш хэш;
  • но НЕ всякое изменение трогает вердикт: часть — только продление срока/формальности. Отпечаток
    требований различает: изменились ли ТОВАРНЫЕ требования (→ перепроверить) или нет (→ вердикт в силе).

Детерминированно, без LLM (квота не нужна). Переизвлечение/перематчинг — отдельно, когда есть квота;
причём срок к тому времени продлён, так что время на перепроверку есть.
"""
from __future__ import annotations

import hashlib
import json
from typing import List, Optional

UNCHANGED = "unchanged"   # ревизия та же — ничего не делаем
FORMAL = "formal"         # источник обновился, но требования те же (продление срока/формальность) → вердикт в силе
MATERIAL = "material"     # изменились товарные требования → вердикт мог смениться, перепроверить


def content_fingerprint(requirements: List[dict]) -> str:
    """Стабильный хэш СОДЕРЖАНИЯ требований (ключ+оператор+значение+единица). Порядко-независим.

    Меняется только при реальном изменении требований, не при перестановке/переформулировке `raw`
    (извлечение недетерминировано по порядку — иначе ловили бы ложные «изменения»)."""
    norm = sorted(
        "%s|%s|%s|%s" % (
            r.get("key"), r.get("operator"),
            json.dumps(r.get("value"), sort_keys=True, ensure_ascii=False),
            r.get("unit", ""),
        )
        for r in (requirements or [])
    )
    return hashlib.sha256(json.dumps(norm, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def classify_change(old_hash: Optional[str], old_ts: Optional[int],
                    new_hash: Optional[str], new_ts: Optional[int]) -> str:
    """Классифицировать изменение закупки между прогонами: UNCHANGED / FORMAL / MATERIAL.

    old/new_ts — метка обновления источника (updateDateTime, epoch ms); old/new_hash — отпечаток
    требований. Логика: изменились требования → MATERIAL (перепроверить вердикт); иначе если источник
    обновился позже → FORMAL (продление/формальность, вердикт в силе); иначе UNCHANGED. Первый прогон
    (нет старых данных) → UNCHANGED (это появление, не изменение)."""
    if old_hash and new_hash and new_hash != old_hash:
        return MATERIAL
    if old_ts and new_ts and new_ts > old_ts:
        return FORMAL
    return UNCHANGED


def change_message(status: str) -> str:
    """Человекочитаемое уведомление под статус (для ленты/алерта клиенту)."""
    return {
        MATERIAL: "ТЗ изменилось — вердикт мог смениться, перепроверьте (срок подачи продлён).",
        FORMAL: "Закупка обновлена (срок/формальности) — вердикт в силе.",
        UNCHANGED: "",
    }.get(status, "")
