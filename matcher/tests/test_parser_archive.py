"""Регресс: .zip-архив вложений распаковывается и внутренние документы читаются
(в т.ч. .xls — проверка рекурсии parse). .zip создаётся из stdlib — фикстура не нужна."""
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from parser import parse  # noqa: E402

XLS = os.path.join(os.path.dirname(__file__), "fixtures", "sample.xls")


def check(name, ok):
    print(("  ✓ " if ok else "  ✗ ") + name)
    return ok


def run():
    tmp = tempfile.mkdtemp()
    zpath = os.path.join(tmp, "attach.zip")
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("тз.txt", "Требование: источник света Светодиод")
        z.write(XLS, "нмцк.xls")           # вложенная таблица → рекурсия parse
    t = parse(zpath)
    r = []
    r.append(check(".zip распарсился", len(t) > 0))
    r.append(check("txt внутри прочитан", "Светодиод" in t and "Требование" in t))
    r.append(check("xls внутри прочитан (рекурсия)", "Диагональ дисплея" in t and "1350" in t))
    r.append(check("имена вложенных файлов помечены", "[ФАЙЛ" in t))
    return r


if __name__ == "__main__":
    res = run(); p = sum(res)
    print("\n%d/%d passed" % (p, len(res)))
    sys.exit(0 if p == len(res) else 1)
