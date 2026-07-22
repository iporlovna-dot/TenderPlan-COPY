"""Тесты keymatch.apply_mapping — детерминированная часть (переименование ключей).
align_keys требует LLM и здесь не тестируется (проверяется на живых прогонах)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from keymatch import apply_mapping


def check(name, ok):
    print(("  ✓ " if ok else "  ✗ ") + name)
    return ok


def main():
    reqs = [
        {"key": "тип_оптики", "operator": "eq", "value": "фиброоптический"},
        {"key": "размер", "operator": "eq", "value": "3"},
        {"key": "назначение", "operator": "eq", "value": "для взрослых"},
    ]
    mapping = {"тип_оптики": "тип_освещения", "размер": "размеры"}
    out = apply_mapping(reqs, mapping)
    r = []

    r.append(check("тип_оптики → тип_освещения", out[0]["key"] == "тип_освещения"))
    r.append(check("размер → размеры", out[1]["key"] == "размеры"))
    r.append(check("назначение без пары — как есть", out[2]["key"] == "назначение"))
    r.append(check("значения сохранены", out[0]["value"] == "фиброоптический"))
    r.append(check("исходный список не мутирован", reqs[0]["key"] == "тип_оптики"))
    r.append(check("пустой маппинг возвращает вход", apply_mapping(reqs, {}) is reqs))

    passed = sum(r)
    print("\n%d/%d passed" % (passed, len(r)))
    sys.exit(0 if passed == len(r) else 1)


if __name__ == "__main__":
    main()
