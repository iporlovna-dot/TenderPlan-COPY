"""Golden-регресс матчера на РЕАЛЬНЫХ кейсах (plan.md Этап 0, README data/golden).

Каждая фикстура в tests/golden/*.json — замороженный вход матчера по живой закупке:
снимок карточки товара + синонимы + требования ПОСЛЕ семантического слоя (align_keys/
align_values) + ожидаемый вердикт/score. Тест собирает Product/Requirement из снимка и
проверяет match() — БЕЗ LLM и сети (align уже применён при генерации фикстуры). Ловит
регрессии ядра матчинга при любой правке `matcher.py`.

Обновление фикстур (после сознательной правки матчера/карточки) — скриптом-генератором,
не руками. Экспертная истина по обоим кейсам: товар поставляли → закупка ПОДХОДИТ."""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from matcher import match  # noqa: E402
from schema import (  # noqa: E402
    Attribute, Hardness, Operator, Product, ReqType, Requirement,
)

GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "golden")


def check(name, ok):
    print(("  ✓ " if ok else "  ✗ ") + name)
    return ok


def _product(pd):
    return Product(id=pd["id"], name=pd["name"],
                   attributes=[Attribute(**a) for a in pd["attributes"]])


def _reqs(rows):
    return [Requirement(key=r["key"], operator=Operator(r["operator"]), value=r.get("value"),
                        unit=r.get("unit"), hardness=Hardness(r.get("hardness", "soft")),
                        type=ReqType(r.get("type", "technical")), raw=r.get("raw", ""))
            for r in rows]


def run_fixture(path):
    fx = json.load(open(path, encoding="utf-8"))
    res = match(_product(fx["product"]), _reqs(fx["requirements"]),
                fx["id"], fx.get("synonyms"))
    tol = fx.get("score_tolerance", 0)
    r = []
    r.append(check("[%s] вердикт = %s" % (fx["id"], fx["expected_verdict"]),
                   res.verdict.value == fx["expected_verdict"]))
    r.append(check("[%s] score %d±%d (факт %d)" % (fx["id"], fx["expected_score"], tol, res.score),
                   abs(res.score - fx["expected_score"]) <= tol))
    return r


def main():
    fixtures = sorted(glob.glob(os.path.join(GOLDEN_DIR, "*.json")))
    if not fixtures:
        print("нет фикстур в tests/golden/")
        sys.exit(1)
    r = []
    for fp in fixtures:
        r += run_fixture(fp)
    passed = sum(r)
    print("\n%d/%d passed (%d фикстур)" % (passed, len(r), len(fixtures)))
    sys.exit(0 if passed == len(r) else 1)


if __name__ == "__main__":
    main()
