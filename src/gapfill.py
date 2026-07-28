"""Дозаполнение пробелов клиентом → пересчёт вердикта (plan.md §Этап 1, фича founder).

Движок даёт вердикт с ПРОБЕЛАМИ (gap: требование есть, данных в карточке нет — не нарушение, а
флаг «подтвердить»). Клиент вносит недостающее значение под конкретный пробел → значение дописывается
в карточку (status `confirmable` — подтверждается клиентом) → матчер пересчитывает %. Ядро матчинга
(`matcher.py`) не трогаем: сюда приходит обновлённая карточка и те же замороженные требования.

Инвариант: пересчёт идёт РЕАЛЬНЫМ матчером на полных требованиях — клиент может ввести значение,
которое всё равно НЕ проходит (ниже порога, чужая единица), и движок это честно покажет, а не
«зачтёт» вслепую.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from matcher import match
from schema import Attribute, Hardness, MatchResult, Operator, Product, ReqType, Requirement

_FILL_DOC = "указано клиентом (дозаполнение пробела)"


def apply_fills(attributes: List[dict], fills: Dict[str, object]) -> List[dict]:
    """Вписать значения клиента в карточку под пробелы. НОВЫЙ список (без мутации входа).

    Значение клиента → status `confirmable` (подтверждается клиентом, не «declared» из спеки).
    Существующий ключ перезаписывается, новый добавляется."""
    out = [dict(a) for a in attributes]
    idx = {a["key"]: i for i, a in enumerate(out)}
    for key, value in (fills or {}).items():
        attr = {"key": key, "value": value, "status": "confirmable", "doc": _FILL_DOC}
        if key in idx:
            out[idx[key]] = attr
        else:
            out.append(attr)
    return out


def _to_requirements(rows: List[dict]) -> List[Requirement]:
    return [Requirement(key=r["key"], operator=Operator(r["operator"]), value=r.get("value"),
                        unit=r.get("unit", ""), hardness=Hardness(r.get("hardness", "soft")),
                        type=ReqType(r.get("type", "technical")), raw=r.get("raw", ""),
                        remapped=r.get("remapped", False),
                        remap_locked=r.get("remap_locked", False))
            for r in rows]


def recompute(product: dict, requirements: List[dict], purchase_id: str,
              fills: Optional[Dict[str, object]] = None,
              synonyms: Optional[dict] = None) -> Tuple[MatchResult, List[dict]]:
    """Пересчитать вердикт с учётом дозаполнения. Возвращает (результат матчинга, обновлённые атрибуты).

    product — карточка как dict ({id, name, attributes:[...]}); requirements — замороженные требования
    закупки (dict'ы, уже после align); fills — {ключ_пробела: значение_клиента}; synonyms — из профиля
    (для семантики значений матчера). fills=None/пусто → просто пересчёт по текущей карточке."""
    attrs = apply_fills(product.get("attributes", []), fills or {})
    prod = Product(id=str(product.get("id", "product")), name=product.get("name", ""),
                   attributes=[Attribute(**a) for a in attrs])
    res = match(prod, _to_requirements(requirements), purchase_id, synonyms)
    return res, attrs


def gap_keys(result: MatchResult) -> List[str]:
    """Ключи требований со статусом gap — то, что клиент МОЖЕТ дозаполнить."""
    from schema import Status
    return [c.req.key for c in result.checks if c.status == Status.GAP]
