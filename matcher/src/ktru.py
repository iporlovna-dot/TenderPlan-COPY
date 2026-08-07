"""Сверка кодов КТРУ товар ↔ позиция закупки (шаг 2 воронки, plan.md §3).

Детерминированный код, БЕЗ LLM, универсален для любой товарки: КТРУ — единый
федеральный каталог, поэтому механизм один на столы, реагенты, клинки.

Код КТРУ: `32.50.13.190-00007686` = ОКПД2-часть `32.50.13.190` + позиция `00007686`.
Заказчики часто указывают только родительский ОКПД2 (`32.50.13.190`) без позиции —
поэтому сверяем с ГРАДАЦИЕЙ, а не по точному равенству:

  exact  — полные коды совпали (та же позиция каталога) → сильнейший сигнал релевантности
  group  — совпала ОКПД2-часть, позиции разные/не указаны → та же товарная группа, уточнять по ТЗ
  none   — ОКПД2-часть не совпала → чужая группа, можно отсекать до дорогого LLM-разбора
"""
from __future__ import annotations

from typing import Iterable, List, Tuple

EXACT = "exact"
GROUP = "group"
NONE = "none"
_RANK = {EXACT: 2, GROUP: 1, NONE: 0}


def split_ktru(code: str) -> Tuple[str, str]:
    """`32.50.13.190-00007686` → (`32.50.13.190`, `00007686`). Без позиции → (окпд2, '')."""
    code = (code or "").strip()
    if "-" in code:
        okpd2, pos = code.split("-", 1)
        return okpd2.strip(), pos.strip()
    return code, ""


def _relation(a: str, b: str) -> str:
    """Отношение двух кодов КТРУ: exact / group / none."""
    oa, pa = split_ktru(a)
    ob, pb = split_ktru(b)
    if not oa or not ob:
        return NONE
    # ОКПД2-часть иерархична: 32.50.13 ⊂ 32.50.13.190 — совпадение по префиксу.
    if not (oa == ob or oa.startswith(ob + ".") or ob.startswith(oa + ".")):
        return NONE
    # ОКПД2 совпал. Позиции: обе указаны и равны → exact; иначе та же группа.
    if pa and pb:
        return EXACT if pa == pb else GROUP
    return GROUP


def ktru_relation(product_codes: Iterable[str], purchase_codes: Iterable[str]) -> str:
    """Лучшее отношение между кодами товара и кодами (позиций) закупки.

    Возвращает сильнейший найденный сигнал: exact > group > none. Для многопозиционных
    лотов достаточно, чтобы совпала ОДНА позиция — товар в лоте присутствует (§11.4).
    """
    best = NONE
    pcs = [c for c in purchase_codes if c]
    for tc in product_codes:
        for pc in pcs:
            rel = _relation(tc, pc)
            if _RANK[rel] > _RANK[best]:
                best = rel
                if best == EXACT:
                    return best
    return best


def relevant(product_codes: Iterable[str], purchase_codes: Iterable[str]) -> bool:
    """Релевантна ли закупка товару по КТРУ (exact или group). none → отсекаем."""
    return ktru_relation(product_codes, purchase_codes) != NONE


def best_position(product_codes: Iterable[str], position_codes: List[str]):
    """Индекс позиции лота, лучше всего совпавшей с товаром по КТРУ (§11.4).

    Матчинг многолота идёт по КОНКРЕТНОЙ позиции — эта функция её и находит: возвращает
    индекс позиции с сильнейшим отношением (exact > group), при равенстве — первую.
    None, если ни одна позиция не релевантна (none) или кодов нет.
    """
    pcs = list(product_codes)
    best_idx, best_rank = None, 0
    for i, code in enumerate(position_codes):
        if not code:
            continue
        rank = _RANK[ktru_relation(pcs, [code])]
        if rank > best_rank:
            best_idx, best_rank = i, rank
            if best_rank == _RANK[EXACT]:
                break
    return best_idx
