"""Тесты серверного матчинга закупок под сохранённый поиск (app/search_match.py).

Запуск: cd server && .venv/bin/python tests/test_search_match.py
(или через pytest). Сеть/БД не трогает — только чистая логика отбора.

Алгоритм отбора зеркалит фронт (site/js/appview.js). Кросс-проверка на живом
снапшоте (5 запросов, расхождение 0) сделана отдельно; здесь — регрессия на
фикстурах, чтобы поймать разъезд при будущих правках.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import search_match as sm  # noqa: E402


def _p(**kw):
    base = {"id": "x", "number": "0", "title": "", "lots": [], "price": 100,
            "law": "44-ФЗ", "endDate": None, "deadlineDays": 10, "stage": "active"}
    base.update(kw)
    return base


NOW = datetime(2026, 8, 24, tzinfo=timezone.utc)
FUTURE = (NOW + timedelta(days=5)).replace(tzinfo=None).isoformat()
PAST = (NOW - timedelta(days=1)).replace(tzinfo=None).isoformat()


def test_stem_and_tokens():
    assert sm.tokens("Перчатки, нитриловые! 0.11мм") == ["перчатки", "нитриловые", "0", "11мм"]
    assert sm.stem("перчатки") == "перчатк"   # отсекается только окончание «и»
    assert sm.stem("мяч") == "мяч"            # короткое слово не режем


def test_fleeting_vowel():
    # «клинок» ↔ «клинки»: беглая гласная в обе стороны
    assert sm.fleeting_vowel_variant("клинок") == "клинк"
    p = _p(title="Клинок ларингоскопа", endDate=FUTURE)
    assert sm.passes_search(p, "клинки", "")           # запрос во мн.ч. ловит ед.ч. в закупке


def test_plus_requires_all_words():
    p = _p(title="Перчатки нитриловые смотровые", endDate=FUTURE)
    assert sm.passes_search(p, "перчатки нитриловые", "")
    assert not sm.passes_search(p, "перчатки латексные", "")   # «латексные» нет — не проходит


def test_minus_excludes():
    p = _p(title="Перчатки латексные опудренные", endDate=FUTURE)
    assert not sm.passes_search(p, "перчатки", "латексные")


def test_haystack_includes_lots_and_number():
    p = _p(title="Расходные материалы", number="0372200",
           lots=[{"name": "Перчатки нитриловые"}], endDate=FUTURE)
    assert sm.passes_search(p, "нитриловые", "")       # слово только в лоте
    assert sm.passes_search(p, "0372200", "")          # и в номере


def test_actionable_filters():
    assert sm.is_actionable(_p(endDate=FUTURE), NOW)
    assert not sm.is_actionable(_p(endDate=PAST), NOW)                       # истёк
    assert not sm.is_actionable(_p(endDate=FUTURE, competitive=False), NOW)  # неконкурентная
    assert not sm.is_actionable(_p(endDate=FUTURE, procStage="Работа комиссии"), NOW)


def test_window_days():
    near = (NOW + timedelta(days=3)).replace(tzinfo=None).isoformat()
    far = (NOW + timedelta(days=40)).replace(tzinfo=None).isoformat()
    assert sm.passes_filters(_p(endDate=near), {"windowDays": 30}, NOW)
    assert not sm.passes_filters(_p(endDate=far), {"windowDays": 30}, NOW)


def test_law_filter():
    assert sm.passes_filters(_p(law="44-ФЗ"), {"law": "44-ФЗ"}, NOW)
    assert not sm.passes_filters(_p(law="223-ФЗ"), {"law": "44-ФЗ"}, NOW)
    assert sm.passes_filters(_p(law="223-ФЗ"), {"law": "all"}, NOW)


def test_purchase_matches_end_to_end():
    search = {"query": "перчатки нитриловые", "minus": "латексные",
              "filters": {"law": "44-ФЗ", "windowDays": 30}}
    good = _p(title="Перчатки нитриловые смотровые", endDate=FUTURE, law="44-ФЗ")
    assert sm.purchase_matches(good, search, NOW)
    # тот же товар, но 223-ФЗ — режется фильтром закона
    assert not sm.purchase_matches(_p(**{**good, "law": "223-ФЗ"}), search, NOW)
    # истёкшая — режется actionable
    assert not sm.purchase_matches(_p(**{**good, "endDate": PAST}), search, NOW)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} тестов пройдено")
