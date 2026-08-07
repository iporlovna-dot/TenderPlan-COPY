"""Регресс: старый бинарный .xls (Excel 97-2003) читается парсером через xlrd.
Фикстура tests/fixtures/sample.xls — маленькая таблица ТЗ. Проверяет, что openpyxl-only
регрессия («does not support the old .xls») не вернётся."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from parser import parse  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "sample.xls")


def check(name, ok):
    print(("  ✓ " if ok else "  ✗ ") + name)
    return ok


def run():
    t = parse(FIX)
    r = []
    r.append(check(".xls распарсился (непустой текст)", len(t) > 0))
    r.append(check("значения таблицы на месте", "Светодиод" in t and "Диагональ дисплея" in t))
    r.append(check("целое не как «1350.0»", "1350" in t and "1350.0" not in t))
    r.append(check("имя листа выведено", "тз" in t.lower()))
    return r


if __name__ == "__main__":
    res = run()
    passed = sum(res)
    print("\n%d/%d passed" % (passed, len(res)))
    sys.exit(0 if passed == len(res) else 1)
