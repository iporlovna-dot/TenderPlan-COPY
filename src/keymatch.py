"""Семантическое сопоставление ИМЁН ключей: требование ТЗ ↔ поле карточки товара.

Проблема: extractor извлекает ключи из текста ТЗ свободно («материал_изготовления»,
«тип_оптики», «размер»), а карточка поставщика называет те же характеристики иначе
(«материал», «тип_освещения», «размеры»). matcher.py сравнивает по имени ключа —
расхождение имён даёт ложные пробелы. См. plan.md §3, §7A.

Решение — универсальное, БЕЗ словаря на категорию: LLM (Haiku, задача `field_equivalence`)
сопоставляет два списка ключей по смыслу. Работает одинаково на клинках, столах, реагентах,
потому что не знает про категорию — судит семантику имён. Слой встаёт МЕЖДУ извлечением и
матчером; ядро (`matcher.py`) не трогаем — инвариант «LLM в ядро не заходит» сохранён.

Прод-замена LLM — эмбеддинги (bge-m3, §13): сопоставление за ~0 ₽. Интерфейс тот же.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

import anthropic

from models import pick_model

_SYSTEM = (
    "Ты сопоставляешь характеристики из ТЗ госзакупки с полями карточки товара поставщика. "
    "Тебе дают поля ТЗ и поля карточки, каждое с именем и значением. Для каждого поля ТЗ найди "
    "поле карточки, обозначающее ТУ ЖЕ характеристику по смыслу. Учитывай И имя, И значение:\n"
    "• по имени: «материал_изготовления»↔«материал», «тип_оптики»↔«тип_освещения», «размер»↔«размеры»;\n"
    "• по значению — это ВАЖНО при расхождении раскладки: если поле ТЗ «тип_клинка=прямой», а в "
    "карточке есть «тип_клинка=миллер» И «форма=прямой», то правильно сопоставить с «форма» "
    "(значение «прямой» — это форма, а не тип). Значение подсказывает, к какому полю относится "
    "характеристика.\n"
    "Если подходящего поля в карточке нет — верни null. НЕ придумывай полей, которых нет в карточке. "
    "Сопоставляй только явные совпадения, не притягивай разное («материал_корпуса»≠«материал_световода»)."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "mapping": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tz_key": {"type": "string"},
                    "card_key": {"type": ["string", "null"]},
                },
                "required": ["tz_key", "card_key"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["mapping"],
    "additionalProperties": False,
}


def align_keys(req_fields: Dict[str, object], product_fields: Dict[str, object],
               client: Optional[anthropic.Anthropic] = None) -> Dict[str, str]:
    """Сопоставить поля ТЗ с полями карточки по имени И значению. -> {ключ_тз: ключ_карточки}.

    Вход — словари {имя_поля: значение} для ТЗ и для карточки. Значения нужны, чтобы разрешать
    расхождения раскладки (ТЗ «тип_клинка=прямой» → карточка «форма=прямой», а не «тип_клинка»).
    В результате только уверенные пары; поля без пары опущены (останутся пробелами в matcher).
    Пустой вход → пустой маппинг (без вызова API).
    """
    if not req_fields or not product_fields:
        return {}

    def _fmt(d):
        return [{"имя": k, "значение": v} for k, v in d.items() if k]

    client = client or anthropic.Anthropic()
    user = json.dumps({"поля_тз": _fmt(req_fields), "поля_карточки": _fmt(product_fields)},
                      ensure_ascii=False, default=str)
    resp = client.messages.create(
        model=pick_model("field_equivalence"),
        max_tokens=2000,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
    )
    text = next((b.text for b in resp.content if b.type == "text"), "")
    data = json.loads(text)
    card_set = set(product_fields)
    return {
        m["tz_key"]: m["card_key"]
        for m in data.get("mapping", [])
        if m.get("card_key") in card_set and m["tz_key"] != m["card_key"]
    }


def apply_mapping(reqs: List[dict], mapping: Dict[str, str]) -> List[dict]:
    """Переименовать ключи требований по маппингу (не тронутые — как есть). Копия, без мутации."""
    if not mapping:
        return reqs
    out = []
    for r in reqs:
        r = dict(r)
        if r.get("key") in mapping:
            r["key"] = mapping[r["key"]]
        out.append(r)
    return out
