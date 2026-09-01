"""Маршрутизация моделей по сложности задачи (plan.md §4) и по провайдеру
(`LK_LLM_PROVIDER`, см. `llmclient.py`).

Принцип: дорогой «разум» — только там, где он нужен. Массовые простые операции гонит
Haiku, извлечение требований из грязных ТЗ — Sonnet, резюме/вердикт и эскалация
спорных случаев — Opus. Это держит себестоимость на копейках при масштабе.

Модуль ЧИСТО конфигурационный: он лишь выбирает id модели, самих вызовов API тут нет.

⚠️ **Провайдер по умолчанию — DeepSeek, не Anthropic.** Anthropic не обслуживает
запросы с VPS (физически в РФ) — 403 forbidden на КАЖДЫЙ реальный вызов, не разовый
сбой ключа (см. CLAUDE.md «Anthropic API — гео-блок»). Anthropic остаётся в коде:
`LK_LLM_PROVIDER=anthropic` включает его обратно одной переменной, без переписывания
вызывающего кода — на случай, если гео-блок снимется, или для сравнения качества.
"""
from __future__ import annotations

import os

# Anthropic (см. claude-api) — активен при LK_LLM_PROVIDER=anthropic.
# Sonnet 5 — рабочая лошадка, Opus 4.8 — топ, Haiku 4.5 — дёшево.
HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-5"
OPUS = "claude-opus-4-8"

# DeepSeek — активен по умолчанию (см. llmclient.py). Только два тира, в отличие от
# Haiku/Sonnet/Opus — усложнять маршрутизацию по задачам под тиры, которых у
# провайдера физически нет, незачем: reasoner берём при явной эскалации (hard=True).
DEEPSEEK_CHAT = "deepseek-chat"
DEEPSEEK_REASONER = "deepseek-reasoner"

# Задача → базовая модель Anthropic. Эскалация сложных случаев поднимает до Opus.
_ANTHROPIC_ROUTES = {
    "parse_price":       HAIKU,   # разбор прайса поставщика при онбординге (простая структуризация)
    "expand_query":      HAIKU,   # расширение поиска синонимами (редко, при настройке профиля)
    "field_equivalence": HAIKU,   # семантическая эквивалентность формулировок полей (короткие суждения)
    "extract":           SONNET,  # извлечение требований из ТЗ — сердце, грязные таблицы
    "verdict":           OPUS,    # резюме/вердикт поставщику — качество важнее цены (по решению пользователя)
    "explain":           OPUS,    # человекочитаемое объяснение по требованиям
}


def pick_model(task: str, hard: bool = False) -> str:
    """Выбрать модель под задачу и активного провайдера (`LK_LLM_PROVIDER`, деф. DeepSeek).

    task: parse_price | expand_query | field_equivalence | extract | verdict | explain.
    hard=True — эскалация сложного/спорного случая на топовую модель провайдера
    (Opus у Anthropic, deepseek-reasoner у DeepSeek). Неизвестная задача при
    Anthropic → Sonnet (безопасный дефолт-баланс).
    """
    provider = os.getenv("LK_LLM_PROVIDER", "deepseek").strip().lower()
    if provider == "anthropic":
        return OPUS if hard else _ANTHROPIC_ROUTES.get(task, SONNET)
    if provider == "deepseek":
        return DEEPSEEK_REASONER if hard else DEEPSEEK_CHAT
    raise ValueError("Неизвестный LK_LLM_PROVIDER=%r (известны: anthropic, deepseek)" % provider)
