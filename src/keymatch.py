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
import re
from typing import Dict, List, Optional

import anthropic

from models import pick_model

_DIGIT_RE = re.compile(r"\d")


def _numeric_scalar(v: object) -> Optional[bool]:
    """Тип значения для сверки маппинга: True — числовой скаляр, False — текстовый скаляр,
    None — не скаляр (список/словарь/None) → проверку типа пропускаем.

    Числовым считаем int/float и строку с цифрой («3,5 В», «6»); булево — текстовым
    («Да»/«Нет» — это enum, не число). Списки (наборы размеров) не типизируем: их
    раскладку сверяет matcher (set/eq), а не имя поля."""
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        return bool(_DIGIT_RE.search(v))
    return None


def _types_compatible(rv: object, cv: object) -> bool:
    """Значения ТЗ и карточки одного типа? Несовместимы, только если ОБА скаляры и один
    числовой, а другой текстовый («Да» ↔ 6) — это признак ошибочного маппинга имён
    (сменные_апертуры=Да ложно легло на количество_апертур=6). Остальное — совместимо."""
    tr, tc = _numeric_scalar(rv), _numeric_scalar(cv)
    return not (tr is not None and tc is not None and tr != tc)

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


def _kname(s: object) -> frozenset:
    """Имя поля → множество токенов (без регистра, ё→е, разделители/единицы-суффиксы сняты).
    Порядок и разделители не важны: «прочность_повышенная»≡«повышенная прочность»,
    «объём_памяти»≡«объем памяти». Множество → сравнение имён морфологически-устойчиво."""
    s = str(s).lower().replace("ё", "е")
    toks = [t for t in re.split(r"[^0-9a-zа-я]+", s) if t]
    return frozenset(toks)


def _deterministic_map(req_fields, product_fields, key_synonyms):
    """Детерминированный слой align_keys БЕЗ LLM: (1) морфологическое равенство имени
    (ё/е, порядок, разделители); (2) словарь групп-синонимов имён по категории. Возвращает
    (mapping, remaining) — уверенные пары и остаток req-полей для LLM. Стабильно и бесплатно,
    это же — фундамент самообучения (словарь копится из подтверждённых пар)."""
    card_keys = list(product_fields)
    card_by_norm = {}
    for ck in card_keys:
        card_by_norm.setdefault(_kname(ck), ck)
    groups = [{_kname(x) for x in g} for g in (key_synonyms or [])]

    mapping, remaining = {}, {}
    for rk, rv in req_fields.items():
        if not rk or rk in product_fields:      # точное совпадение — matcher сам
            continue
        nk = _kname(rk)
        hit = card_by_norm.get(nk)              # (1) морфология
        if hit is None:                          # (2) группа-синоним
            for g in groups:
                if nk in g:
                    hit = next((ck for ck in card_keys if _kname(ck) in g), None)
                    if hit:
                        break
        if hit and hit != rk and _types_compatible(rv, product_fields.get(hit)):
            mapping[rk] = hit
        else:
            remaining[rk] = rv
    return mapping, remaining


def align_keys(req_fields: Dict[str, object], product_fields: Dict[str, object],
               client: Optional[anthropic.Anthropic] = None,
               key_synonyms: Optional[List[list]] = None) -> Dict[str, str]:
    """Сопоставить поля ТЗ с полями карточки по имени И значению. -> {ключ_тз: ключ_карточки}.

    Двухслойно: сперва ДЕТЕРМИНИРОВАННЫЙ слой (морфология имени + словарь `key_synonyms` из
    профиля) — стабильно и бесплатно; LLM (Haiku) вызывается ТОЛЬКО на нераспознанном остатке.
    Значения нужны, чтобы разрешать расхождения раскладки и отсекать маппинг разного типа.
    Пустой вход → пустой маппинг (без вызова API).
    """
    if not req_fields or not product_fields:
        return {}

    mapping, remaining = _deterministic_map(req_fields, product_fields, key_synonyms)
    if not remaining:
        return mapping                          # всё сведено детерминированно — LLM не нужен

    def _fmt(d):
        return [{"имя": k, "значение": v} for k, v in d.items() if k]

    client = client or anthropic.Anthropic()
    user = json.dumps({"поля_тз": _fmt(remaining), "поля_карточки": _fmt(product_fields)},
                      ensure_ascii=False, default=str)
    resp = client.messages.create(
        model=pick_model("field_equivalence"),
        max_tokens=8000,  # большой ТЗ (десятки полей) → маппинг не влезал в 2000 → обрыв JSON
        system=_SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
    )
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return mapping  # обрыв ответа модели — оставляем детерминированный слой, не роняем прогон
    card_set = set(product_fields)
    for m in data.get("mapping", []):
        tz, cd = m.get("tz_key"), m.get("card_key")
        if (cd in card_set and tz != cd and tz in remaining
                # отсекаем маппинг с расхождением типа значения (строка↔число)
                and _types_compatible(remaining.get(tz), product_fields.get(cd))):
            mapping[tz] = cd
    return mapping


_VAL_SYSTEM = (
    "Ты сверяешь ЗНАЧЕНИЯ характеристик: удовлетворяет ли значение из карточки товара "
    "требованию ТЗ по смыслу (не по строке). Примеры «да»: «Интубация пациентов»↔«интубация "
    "трахеи»; «металл»↔«нержавеющая сталь» (сталь это металл); «соответствие»/«наличие»↔«да»; "
    "«новый, не бывший в эксплуатации»↔«новый, неиспользованный». Примеры «нет»: «прямой "
    "Миллер»↔«изогнутый» (форма противоположна); «фиброоптический»↔«лампочный/стандартный» "
    "(разный тип освещения); «полиамид»↔«нержавеющая сталь». Числа/диапазоны НЕ оценивай — "
    "их проверяет код. Для каждого поля верни satisfies: true/false."
)
_VAL_SCHEMA = {
    "type": "object",
    "properties": {
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "satisfies": {"type": "boolean"},
                },
                "required": ["key", "satisfies"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["checks"],
    "additionalProperties": False,
}


def align_values(reqs: List[dict], product, client: Optional[anthropic.Anthropic] = None) -> List[dict]:
    """Семантическая сверка ЗНАЧЕНИЙ: где значение карточки удовлетворяет требованию по смыслу,
    подменяем значение требования на карточное — тогда строковый matcher засчитает pass.

    Оцениваем только строковые eq-требования, у которых ключ есть в карточке (числа/диапазоны
    остаются коду). Не тронутые требования возвращаются как есть. Копия, без мутации.
    """
    pairs = []  # (idx, key, req_val, card_val)
    for i, r in enumerate(reqs):
        attr = product.get(r.get("key"))
        if attr is None:
            continue
        rv, cv = r.get("value"), attr.value
        if not isinstance(rv, str) or not isinstance(cv, str):
            continue  # числа/списки — коду
        if rv.strip().lower() == cv.strip().lower():
            continue  # уже совпадают строково
        pairs.append((i, r["key"], rv, cv))

    if not pairs:
        return reqs

    client = client or anthropic.Anthropic()
    payload = [{"key": k, "требование_тз": rv, "значение_карточки": cv} for _, k, rv, cv in pairs]
    resp = client.messages.create(
        model=pick_model("field_equivalence"),
        max_tokens=8000,  # много пар на большом ТЗ → 2000 обрывало JSON
        system=_VAL_SYSTEM,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        output_config={"format": {"type": "json_schema", "schema": _VAL_SCHEMA}},
    )
    try:
        data = json.loads(next((b.text for b in resp.content if b.type == "text"), "{}"))
    except json.JSONDecodeError:
        return reqs  # обрыв ответа модели — оставляем требования как есть, не роняем прогон
    ok_keys = {c["key"] for c in data.get("checks", []) if c.get("satisfies")}

    out = [dict(r) for r in reqs]
    for i, key, _rv, cv in pairs:
        if key in ok_keys:
            out[i]["value"] = cv  # нормализуем к карточному → matcher засчитает pass
    return out


def apply_mapping(reqs: List[dict], mapping: Dict[str, str],
                  critical: Optional[List[str]] = None) -> List[dict]:
    """Переименовать ключи требований по маппингу (не тронутые — как есть). Копия, без мутации.

    Каждое переименованное требование помечаем `remapped=True` (ключ пришёл из семантики,
    не дословно) и `remap_locked` — попадал ли ИСХОДНЫЙ ключ ТЗ в critical_attributes профиля.
    Флаги читает matcher: несоответствие по remapped-и-НЕ-locked ключе → пробел, не нарушение
    (plan §3.6в). По critical маппинг заперт — там ложную дисквалификацию не смягчаем."""
    if not mapping:
        return reqs
    crit = {str(c) for c in (critical or [])}
    out = []
    for r in reqs:
        r = dict(r)
        k = r.get("key")
        if k in mapping:
            r["remapped"] = True
            r["remap_locked"] = k in crit
            r["key"] = mapping[k]
        out.append(r)
    return out
