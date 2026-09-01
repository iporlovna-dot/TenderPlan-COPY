"""Извлечение требований из текста ТЗ (шаг 6 конвейера, plan.md §3) — сердце №1.

LLM со строгой JSON-схемой: грязный текст ТЗ → requirements[] в формате
schema.Requirement (см. plan.md §5.2). В само ядро матчинга LLM не заходит — сюда
приходит текст, отсюда уходит структура, дальше matcher.py работает уже без LLM.

Провайдер и модель — через `llmclient.py`/`models.py` (`LK_LLM_PROVIDER`, по
умолчанию DeepSeek; Anthropic доступен флагом, см. `models.py`). Ключ — в окружении
(`DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY`).

Использование:
    from parser import parse
    from extractor import extract_requirements
    reqs = extract_requirements(parse("data/samples/gloves_tender_01.docx"))
"""
from __future__ import annotations

import json
from typing import List, Optional

import llmclient
from models import pick_model  # маршрутизация моделей по сложности и провайдеру (plan.md §4)

# Строгая схема вывода: массив требований ровно в форме schema.Requirement.
# value полиморфен (число / строка / bool / список) — операторам матчинга это ок.
_REQ_ITEM = {
    "type": "object",
    "properties": {
        "key": {"type": "string"},
        "operator": {
            "type": "string",
            "enum": ["eq", "gte", "lte", "range", "one_of", "set", "present"],
        },
        "value": {
            "anyOf": [
                {"type": "string"},
                {"type": "number"},
                {"type": "boolean"},
                {"type": "array", "items": {"type": "string"}},
                {"type": "array", "items": {"type": "number"}},
            ]
        },
        "unit": {"type": "string"},
        "hardness": {"type": "string", "enum": ["hard", "soft"]},
        "type": {"type": "string", "enum": ["technical", "documentary"]},
        "raw": {"type": "string"},
    },
    "required": ["key", "operator", "value", "unit", "hardness", "type", "raw"],
    "additionalProperties": False,
}

_SCHEMA = {
    "type": "object",
    "properties": {"requirements": {"type": "array", "items": _REQ_ITEM}},
    "required": ["requirements"],
    "additionalProperties": False,
}

# Схема для многолота: массив позиций, у каждой имя + свои требования.
_POS_SCHEMA = {
    "type": "object",
    "properties": {
        "positions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "requirements": {"type": "array", "items": _REQ_ITEM},
                },
                "required": ["name", "requirements"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["positions"],
    "additionalProperties": False,
}

_SYSTEM = """\
Ты извлекаешь требования из технического задания госзакупки (44-ФЗ / 223-ФЗ) в строгий JSON.

Верни массив requirements[], по одному объекту на КАЖДОЕ проверяемое требование к товару.
Поля объекта:
- key: короткий машинный ключ характеристики на русском, snake_case
  (материал, толщина_пальцы_мм, срок_годности_остаточный_мес, размеры, рег_удостоверение).
  Нормализуй ключ: нижний регистр, слова через «_», без пробелов/дефисов, без кавычек.
  Одинаковые по смыслу характеристики называй одинаково между закупками — НЕ придумывай
  новых синонимов имени, если та же характеристика уже есть в списке «канонические_имена_ключей»
  профиля: бери имя ОТТУДА дословно. Это критично для повторяемости извлечения.
- operator — как сравнивать значение товара с требованием:
  * eq       — точное значение (материал = «нитрильный латекс», опудренность = «нет»)
  * gte      — не менее / ≥ / от (толщина ≥ 0,11)
  * lte      — не более / ≤ / до (AQL ≤ 1,5)
  * range    — диапазон [min, max]
  * one_of   — значение товара должно попасть в допустимый набор ТЗ
  * set      — товар должен покрыть ВЕСЬ набор (размеры S, M, L)
  * present  — характеристика/документ просто должен наличествовать (value=true)
- value: число для gte/lte; [min,max] для range; список для one_of/set;
  строка для eq; true для present. Десятичную запятую переводи в число (0,11 -> 0.11).
- unit: единица измерения ("мм", "мес", "") — пусто, если нет.
- hardness — ЖЁСТКОСТЬ требования. Это критично для дисквалификации:
  * hard — если в ТЗ рядом стоит «Значение характеристики не может изменяться
    участником закупки», либо это обязательный документ/условие допуска.
  * soft — если «Участник закупки указывает в заявке конкретное значение
    характеристики» или требование мягкое.
  Колонка «Инструкция по заполнению» в таблице ТЗ прямо задаёт hardness — читай её.
- type: technical (характеристика товара) | documentary (рег. удостоверение,
  срок годности, новизна товара, сертификат).
- raw: короткая дословная формулировка требования из ТЗ (как в документе).

Правила:
- Не выдумывай требований, которых нет в тексте. Не дублируй.
- Значения количества/поставки (сколько пар, цена) — это НЕ требования к товару, пропускай.
- Если характеристика содержит несколько независимых требований («без опудривания,
  не обладают антибактериальными свойствами»), разбей на отдельные объекты.\
"""


def _canonical_keys(profile: dict) -> List[str]:
    """Контролируемый словарь ИМЁН ключей из профиля — то, что надо переиспользовать дословно.

    Берём только поля, значения которых суть имена характеристик/документов:
    `critical_attributes` (материал, стерильность…) + `required_documents` (рег_удостоверение).
    НЕ берём `synonyms` — там варианты ЗНАЧЕНИЙ (нитрил→нитриловый), а не имена полей.
    Отсортировано и без дублей — детерминированный порядок держит промпт байт-стабильным
    (важно для prompt-кэша). См. plan.md §3.2 «стабильность извлечения».
    """
    keys = list(profile.get("critical_attributes") or []) + list(profile.get("required_documents") or [])
    return sorted({k for k in keys if isinstance(k, str) and k.strip()})


def extract_requirements(
    tz_text: str,
    profile: Optional[dict] = None,
    model: Optional[str] = None,
    hard: bool = False,
    client=None,
    position: Optional[dict] = None,
) -> List[dict]:
    """Текст ТЗ → список требований (dict'ы формата schema.Requirement).

    profile — опциональный профиль категории (data/profiles/*.json): его словарь
    характеристик/синонимов подсказывает модели канонические ключи. См. plan.md §7A.
    position — если ТЗ описывает СБОРНЫЙ ЛОТ, скоупит извлечение на одну позицию
    (`{"name": "...", "others": [...]}`): требования к другим позициям лота НЕ
    извлекаются. Иначе (одиночный лот / позиция не определена) — по всему ТЗ.
    Это чинит занижение % на многолотах: товар скорится против требований СВОЕЙ
    позиции, а не всего лота (plan.md §3.4a).
    model — переопределить модель; по умолчанию маршрутизатор даёт Sonnet 5 (extract),
    hard=True эскалирует спорное/сложное ТЗ на Opus. Результат готов к записи в
    data/requirements/*.json и подаче в matcher.match.
    """
    model = model or pick_model("extract", hard=hard)
    client = client or llmclient.get_default_client()

    user = "Текст ТЗ:\n\n" + tz_text
    if position and position.get("name"):
        others = position.get("others") or []
        user += (
            "\n\nЭТО СБОРНЫЙ ЛОТ ИЗ НЕСКОЛЬКИХ ПОЗИЦИЙ. Извлекай требования ТОЛЬКО к позиции: "
            "«%s». НЕ включай характеристики других позиций лота: %s. Требования, общие для "
            "всей поставки (документы, срок годности, новизна), включай."
            % (position["name"], "; ".join(others) if others else "—")
        )
    if profile:
        hint = {
            "категория": profile.get("category"),
            "канонические_имена_ключей": _canonical_keys(profile),
            "синонимы_значений": profile.get("synonyms"),
            "критические_дисквалификаторы": profile.get("critical_attributes"),
            "обязательные_документы": profile.get("required_documents"),
        }
        user += (
            "\n\nПрофиль категории. Извлекай ТОЛЬКО то, что реально в тексте. Но если "
            "характеристика соответствует одному из «канонические_имена_ключей» — используй "
            "это имя ключа ДОСЛОВНО (не переименовывай, не добавляй синонимов):\n"
            + json.dumps(hint, ensure_ascii=False)
        )

    data = llmclient.structured_create(client, model, _SYSTEM, user, _SCHEMA, max_tokens=16000)
    return data["requirements"]


_POS_SYSTEM = _SYSTEM + """

ОСОБЫЙ РЕЖИМ — МНОГОПОЗИЦИОННЫЙ ЛОТ. ТЗ описывает НЕСКОЛЬКО позиций закупки (напр. клинки
разного типа/размера, или разные изделия: клинок, рукоятка, сумка). Верни positions[] — по
объекту на КАЖДУЮ позицию: {name: краткое имя позиции, requirements: [...требования этой
позиции по правилам выше...]}. Раздели позиции по строкам таблицы / вариантам товара:
одноимённые позиции с разными размерами — это РАЗНЫЕ позиции. Требования, общие для всей
поставки (документы), можно продублировать в каждую позицию или опустить.\
"""


def extract_positions(
    tz_text: str,
    profile: Optional[dict] = None,
    model: Optional[str] = None,
    hard: bool = False,
    client=None,
) -> List[dict]:
    """Многолот → список позиций [{name, requirements:[dict]}]. Один LLM-вызов на весь ТЗ.

    Формат-независимый разбор лота: модель сама читает таблицу ТЗ (любого формата) и делит
    её на позиции с их характеристиками, включая одноимённые позиции разных размеров (что
    детерминированный табличный парсер и КТРУ-скоуп не могут). Для lot_coverage.py."""
    model = model or pick_model("extract", hard=hard)
    client = client or llmclient.get_default_client()

    user = "Текст ТЗ:\n\n" + tz_text
    if profile:
        hint = {"категория": profile.get("category"),
                "канонические_имена_ключей": _canonical_keys(profile),
                "синонимы_значений": profile.get("synonyms")}
        user += ("\n\nПрофиль (канонические имена ключей — используй ДОСЛОВНО, где подходит):\n"
                 + json.dumps(hint, ensure_ascii=False))

    data = llmclient.structured_create(client, model, _POS_SYSTEM, user, _POS_SCHEMA, max_tokens=16000)
    return data["positions"]


if __name__ == "__main__":
    import sys

    from parser import parse

    profile = None
    if len(sys.argv) > 2:
        with open(sys.argv[2], encoding="utf-8") as f:
            profile = json.load(f)

    reqs = extract_requirements(parse(sys.argv[1]), profile=profile)
    print(json.dumps({"requirements": reqs}, ensure_ascii=False, indent=2))
