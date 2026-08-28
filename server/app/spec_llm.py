"""LLM-извлечение требований из названия позиции — фолбэк там, где ktrutable.js
(таблично-эвристический парсер ТЗ) не нашёл структурированных характеристик.

Зачем. Замер 2026-08-28 на живом spec.json: 87% позиций 44-ФЗ и 98% 223-ФЗ
приходят с `chars: []` — goods-фолбэк ktrutable.js («Наименование|Кол-во» без
колонки характеристик). Но у трети таких позиций (33%, name ≥ 60 символов) само
название УЖЕ содержит характеристики текстом — заказчик пишет их прямо в ячейке
товара, не в отдельной колонке («Материал: фанера высшего сорта... Размеры:
75х40 см...»). Экстрактор `matcher/src/extractor.py` читает это не хуже
структурированной таблицы — проверено вживую на реальных позициях корпуса.

Почему лениво, а не батчем при сборе. Позиций без характеристик — 116 тыс.
Гонять LLM на каждую при каждом часовом прогоне сборщика — грубое расточительство:
подавляющее большинство никогда не попадёт на глаза пользователю. Здесь же вызов
происходит только когда матчер (`spec_match.match_lot`) уже выбрал КОНКРЕТНЫЙ
товар пользователя под КОНКРЕТНУЮ позицию — то есть объём естественно ограничен
реальным просмотром, а не размером корпуса. Кэш (БД, по хэшу названия) следующий
показ той же позиции (или того же названия в другой закупке) отдаёт бесплатно.

Порог длины (NAME_MIN) — не тратить вызов на «Бахилы одноразовые»: в коротком
названии характеристикам взяться неоткуда, а извлечение стоит денег.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from typing import List

from . import db

_MATCHER_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "matcher", "src")
if _MATCHER_SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_MATCHER_SRC))

log = logging.getLogger("lekalo.spec_llm")

ENABLED = os.getenv("LK_SPEC_LLM", "1") != "0" and bool(os.getenv("ANTHROPIC_API_KEY"))
NAME_MIN = int(os.getenv("LK_SPEC_LLM_NAME_MIN", "60"))
TIMEOUT_S = float(os.getenv("LK_SPEC_LLM_TIMEOUT", "25"))

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(timeout=TIMEOUT_S)
    return _client


def _hash(name: str) -> str:
    return hashlib.sha256(name.strip().lower().encode("utf-8")).hexdigest()


def _cache_get(name: str):
    h = _hash(name)
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT requirements FROM spec_llm_cache WHERE name_hash = ?", (h,)
        ).fetchone()
        return json.loads(row["requirements"]) if row else None
    finally:
        conn.close()


def _cache_put(name: str, reqs: List[dict]) -> None:
    h = _hash(name)
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO spec_llm_cache (name_hash, name, requirements, created_at) "
            "VALUES (?, ?, ?, ?)",
            (h, name[:500], json.dumps(reqs, ensure_ascii=False), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        )
        conn.commit()
    finally:
        conn.close()


def should_try(name: str) -> bool:
    """Стоит ли вообще звать LLM на это название — до кэша и до сети, дёшево."""
    return ENABLED and bool(name) and len(name.strip()) >= NAME_MIN


def extract_cached(name: str, client=None) -> List[dict]:
    """Название позиции → требования (формат schema.Requirement, тот же, что у
    ktrutable.js chars). Кэш по хэшу названия. Пустой список — либо LLM честно
    ничего не нашла (кэшируется), либо звать не стоило/сеть подвела (НЕ кэшируется,
    см. db.py). Разница вызывающему не видна и не нужна: оба раза — «сверить
    нечем сейчас», а спецматч уже честно помечает такую позицию `unverified`.

    client — инъекция для тестов (см. matcher/tests/_llm_mock.py ReqsLLM), тем
    же приёмом, что у extractor.extract_requirements. Без инъекции — боевой
    anthropic.Anthropic() из окружения (ANTHROPIC_API_KEY)."""
    if not should_try(name):
        return []

    cached = _cache_get(name)
    if cached is not None:
        return cached

    try:
        from extractor import extract_requirements
        reqs = extract_requirements(name, client=client or _get_client())
    except Exception as e:  # сеть, лимиты, отказ модели, битый JSON — что угодно
        log.warning("spec_llm: извлечение упало (%s): %s", name[:80], e)
        return []

    _cache_put(name, reqs)
    return reqs
