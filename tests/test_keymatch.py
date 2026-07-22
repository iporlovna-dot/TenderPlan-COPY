"""Тесты keymatch: apply_mapping (детерминированное переименование ключей) и
align_values (семантическая сверка значений — с моком LLM, без сети).
align_keys требует LLM и здесь не тестируется (проверяется на живых прогонах)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from _llm_mock import ChecksLLM as FakeLLM  # общий мок anthropic-клиента (tests/_llm_mock.py)
from keymatch import apply_mapping, align_values
from schema import Attribute, Product


def check(name, ok):
    print(("  ✓ " if ok else "  ✗ ") + name)
    return ok


def test_apply_mapping():
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
    return r


def test_align_values():
    product = Product(id="p", name="Клинок", attributes=[
        Attribute(key="материал", value="нержавеющая сталь"),
        Attribute(key="тип_освещения", value="фиброоптический"),
        Attribute(key="размер", value=3),                      # число
        Attribute(key="назначение", value="интубация трахеи"),
    ])
    reqs = [
        {"key": "материал", "operator": "eq", "value": "металл"},              # семантич. → true
        {"key": "тип_освещения", "operator": "eq", "value": "лампочный"},      # семантич. → false
        {"key": "размер", "operator": "gte", "value": 3},                      # число в карточке → пропуск
        {"key": "назначение", "operator": "eq", "value": "интубация трахеи"},  # уже строково равно → пропуск
        {"key": "форма", "operator": "eq", "value": "прямой"},                 # нет в карточке → пропуск
    ]
    llm = FakeLLM(ok_keys={"материал"})
    out = align_values(reqs, product, client=llm)
    r = []

    r.append(check("в LLM ушли только строковые несовпадающие пары",
                   llm.last_keys == {"материал", "тип_освещения"}))
    r.append(check("ровно один вызов API", llm.calls == 1))
    r.append(check("satisfies=true → значение нормализовано к карточному",
                   out[0]["value"] == "нержавеющая сталь"))
    r.append(check("satisfies=false → значение не тронуто", out[1]["value"] == "лампочный"))
    r.append(check("число не тронуто (остаётся коду)", out[2]["value"] == 3))
    r.append(check("уже совпадающее не тронуто", out[3]["value"] == "интубация трахеи"))
    r.append(check("ключа нет в карточке — не тронуто", out[4]["value"] == "прямой"))
    r.append(check("исходные требования не мутированы", reqs[0]["value"] == "металл"))

    # нет пар для оценки → возврат входа без вызова API
    llm2 = FakeLLM(ok_keys=set())
    same = align_values([{"key": "размер", "operator": "gte", "value": 3}], product, client=llm2)
    r.append(check("нет строковых пар → API не вызывается", llm2.calls == 0))
    r.append(check("пустой вход требований → возврат как есть", align_values([], product, client=llm2) == []))
    return r


def main():
    r = test_apply_mapping() + test_align_values()
    passed = sum(r)
    print("\n%d/%d passed" % (passed, len(r)))
    sys.exit(0 if passed == len(r) else 1)


if __name__ == "__main__":
    main()
