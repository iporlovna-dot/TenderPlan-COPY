"""Семантический отсев воронки (Шаг 3, plan.md §Шаг 3, §10 строка 3) — локальные эмбеддинги.

Проблема keyword-фильтра (`filter.py`): он ловит закупки по курируемым основам слов («ларингоскоп»,
«клинок»), но пропускает те, где заказчик назвал то же ИНАЧЕ («устройство для интубации с видеоканалом»
мимо ключа «ларингоскоп»). Семантическое сходство «предмет закупки ↔ товар» на уровне ЦЕЛОГО описания
это ловит. Здесь эмбеддинги в своей стихии: сравниваем документы, а не пары значений — проблемы
антонимов (как в align_values) НЕТ.

Место в воронке: ПОСЛЕ грубого кода/слов, ДО забора и разбора ТЗ. По умолчанию РАНЖИРУЕТ (ничего не
теряя) — drop-порог опционален и требует калибровки на живых данных (`embed-calibrate`), иначе рискуем
терять релевантное. Пустой эмбеддер (нет пакета/модели) → мягкая деградация: порядок не меняем.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from embed import cosine_matrix, default_embedder
from schema import Purchase

# Ключи характеристик, чьи ЗНАЧЕНИЯ несут «что это за товар» (идентичность), а не числовую метрику —
# добавляем их в текст товара, чтобы сходство считалось по сути, а не только по названию карточки.
_IDENTITY_KEYS = ("тип_клинка", "тип_прибора", "тип_изделия", "исполнение", "назначение", "категория")


def _attrs(product) -> list:
    return product.attributes if hasattr(product, "attributes") else product.get("attributes", [])


def _name(product) -> str:
    return product.name if hasattr(product, "name") else product.get("name", "")


def product_text(product, extra: Optional[str] = None) -> str:
    """Представление товара для эмбеддинга: название + идентифицирующие характеристики (+extra).

    extra — контекст категории (напр. `profile['category']` или ключевые слова), усиливает сигнал."""
    parts = [_name(product)]
    for a in _attrs(product):
        key = a.key if hasattr(a, "key") else a.get("key")
        val = a.value if hasattr(a, "value") else a.get("value")
        if key in _IDENTITY_KEYS and isinstance(val, str):
            parts.append(val)
    if extra:
        parts.append(str(extra))
    return ". ".join(p for p in parts if p)


def subject_text(purchase: Purchase) -> str:
    return purchase.subject or ""


def rank(purchases: List[Purchase], product, embedder=None,
         extra: Optional[str] = None) -> List[Tuple[Purchase, Optional[float]]]:
    """Ранжировать закупки по семантическому сходству предмета с товаром (по убыванию).

    Ничего не отсекает — только сортирует (безопасно, кандидаты не теряются). Эмбеддер недоступен →
    возвращаем как есть с sim=None (мягкая деградация). Пустой вход → пустой список."""
    if not purchases:
        return []
    embedder = embedder if embedder is not None else default_embedder()
    if embedder is None:
        return [(p, None) for p in purchases]
    pv = embedder.encode([product_text(product, extra)])
    sv = embedder.encode([subject_text(p) for p in purchases])
    sims = cosine_matrix(pv, sv)[0]
    scored = [(p, float(sims[i])) for i, p in enumerate(purchases)]
    scored.sort(key=lambda ps: ps[1], reverse=True)
    return scored


def filter_relevant(purchases: List[Purchase], product, min_sim: float, embedder=None,
                    extra: Optional[str] = None) -> List[Tuple[Purchase, float]]:
    """Оставить закупки с сходством ≥ min_sim (отсортировано). ВНИМАНИЕ: min_sim требует калибровки
    на живых данных (`embed-calibrate`) — иначе можно потерять релевантное. Без калибровки используй
    `rank` (без отсечения). Эмбеддер недоступен → не отсекаем (возвращаем всё, sim=0.0)."""
    scored = rank(purchases, product, embedder, extra)
    out = []
    for p, s in scored:
        if s is None:            # деградация без модели — не теряем кандидатов
            out.append((p, 0.0))
        elif s >= min_sim:
            out.append((p, s))
    return out
