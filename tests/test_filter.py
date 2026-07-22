"""Тесты грубого фильтра воронки (шаг 2): коды классификаторов + ключевые слова."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import time  # noqa: E402

from schema import Purchase  # noqa: E402
from filter import coarse_filter, score_purchase, in_region, deadline_in_window  # noqa: E402

_DAY = 86_400_000  # мс в сутках

PROFILE = {
    "okpd2_ktru": ["22.19.60.119"],
    "keywords": ["перчат", "нитрил", "смотров"],   # различительные основы
    "synonyms": {"нитрил": ["нитрильный латекс", "нитриловый"]},  # для matcher, не discovery
}


def _p(pid, subject="", okpd2=None, ktru=None):
    return Purchase(id=pid, subject=subject, okpd2=okpd2 or [], ktru=ktru or [])


def test_code_prefix_match_scores_high():
    # код закупки — потомок кода профиля
    p = _p("1", okpd2=["22.19.60.119-00000008"])
    assert score_purchase(p, PROFILE) >= 10.0


def test_keyword_only_match_scores_low_but_passes():
    p = _p("2", subject="Поставка перчаток нитриловых смотровых")
    s = score_purchase(p, PROFILE)
    assert 0 < s < 10.0  # ключевые слова есть, кода нет


def test_stem_matches_inflections():
    # основа «нитрил» ловит «нитрильного», «перчат» — «Перчатки»
    p = _p("3", subject="Перчатки из нитрильного латекса")
    assert score_purchase(p, PROFILE) >= 1.0


def test_no_false_positive_on_common_prefix():
    # регрессия: раньше синоним «без» подмешивался и матчил «безопасности»
    p = _p("3b", subject="Поставка бокса микробиологической безопасности класса II")
    assert score_purchase(p, PROFILE) == 0.0


def test_irrelevant_purchase_filtered_out():
    p = _p("4", subject="Поставка канцелярских товаров", okpd2=["17.23.13"])
    assert score_purchase(p, PROFILE) == 0.0
    assert coarse_filter([p], PROFILE) == []


def test_filter_sorts_by_relevance():
    code = _p("code", subject="перчатки", okpd2=["22.19.60.119"])   # код + слово
    kw = _p("kw", subject="перчатки нитрил смотровые")              # только слова
    junk = _p("junk", subject="бумага А4")
    out = coarse_filter([kw, junk, code], PROFILE)
    ids = [p.id for p, _ in out]
    assert ids == ["code", "kw"]  # junk отсеян, код-совпадение выше


def test_case_and_yo_insensitive():
    p = _p("5", subject="ПЕРЧАТКИ СМОТРОВЫЕ")
    assert score_purchase(p, PROFILE) >= 1.0


def _p_deadline(days_from_now, region="77"):
    p = Purchase(id="d", subject="перчатки")
    p.region = region
    p.submission_close = int(time.time() * 1000) + int(days_from_now * _DAY)
    return p


def test_region_filter():
    p = _p_deadline(10, region="77")
    assert in_region(p, None) is True          # вся РФ
    assert in_region(p, ["77", "54"]) is True   # Москва в наборе
    assert in_region(p, ["54"]) is False        # только Новосибирск — не проходит


def test_deadline_window():
    assert deadline_in_window(_p_deadline(10)) is True    # 10 дней — в окне [1,29]
    assert deadline_in_window(_p_deadline(40)) is False   # 40 дней — слишком далеко
    assert deadline_in_window(_p_deadline(-1)) is False   # уже закрыта
    p = Purchase(id="x", subject="перчатки")              # нет даты подачи
    assert deadline_in_window(p) is False


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn()
            print("✅ %s" % fn.__name__)
            passed += 1
        except Exception:
            print("❌ %s" % fn.__name__)
            traceback.print_exc()
    print("\n%d/%d тестов прошли" % (passed, len(fns)))
    sys.exit(0 if passed == len(fns) else 1)
