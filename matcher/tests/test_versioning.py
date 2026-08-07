"""Тесты версионности (src/versioning.py): отпечаток требований + классификация изменения."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from versioning import (FORMAL, MATERIAL, UNCHANGED, change_message,
                        classify_change, content_fingerprint)


def check(name, ok):
    print(("  ✓ " if ok else "  ✗ ") + name)
    return ok


R1 = {"key": "материал", "operator": "eq", "value": "нитрил", "unit": ""}
R2 = {"key": "толщина", "operator": "gte", "value": 0.11, "unit": "мм"}


def test_fingerprint_order_independent():
    a = content_fingerprint([R1, R2])
    b = content_fingerprint([R2, R1])              # другой порядок
    r = []
    r.append(check("перестановка требований → тот же отпечаток", a == b))
    r.append(check("непустой отпечаток", len(a) == 16))
    r.append(check("пустой набор → стабильный отпечаток",
                   content_fingerprint([]) == content_fingerprint([])))
    return r


def test_fingerprint_changes_on_value():
    a = content_fingerprint([R2])
    b = content_fingerprint([{"key": "толщина", "operator": "gte", "value": 0.13, "unit": "мм"}])
    return [check("изменение значения → другой отпечаток", a != b)]


def test_classify():
    h1, h2 = content_fingerprint([R1]), content_fingerprint([R1, R2])
    r = []
    r.append(check("та же ревизия → UNCHANGED", classify_change(h1, 100, h1, 100) == UNCHANGED))
    r.append(check("требования изменились → MATERIAL", classify_change(h1, 100, h2, 100) == MATERIAL))
    r.append(check("источник новее, требования те же → FORMAL",
                   classify_change(h1, 100, h1, 200) == FORMAL))
    r.append(check("новее И требования другие → MATERIAL (важное важнее)",
                   classify_change(h1, 100, h2, 200) == MATERIAL))
    r.append(check("первый прогон (нет старого) → UNCHANGED",
                   classify_change(None, None, h1, 100) == UNCHANGED))
    return r


def test_messages():
    r = []
    r.append(check("MATERIAL → уведомление про перепроверку",
                   "перепровер" in change_message(MATERIAL).lower()))
    r.append(check("FORMAL → уведомление «вердикт в силе»", "в силе" in change_message(FORMAL).lower()))
    r.append(check("UNCHANGED → пусто", change_message(UNCHANGED) == ""))
    return r


def main():
    r = (test_fingerprint_order_independent() + test_fingerprint_changes_on_value()
         + test_classify() + test_messages())
    passed = sum(r)
    print("\n%d/%d passed" % (passed, len(r)))
    sys.exit(0 if passed == len(r) else 1)


if __name__ == "__main__":
    main()
