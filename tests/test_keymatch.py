"""Тесты keymatch: apply_mapping (детерминированное переименование ключей) и
align_values (семантическая сверка значений — с моком LLM, без сети).
align_keys требует LLM и здесь не тестируется (проверяется на живых прогонах)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from _llm_mock import ChecksLLM as FakeLLM  # общий мок anthropic-клиента (tests/_llm_mock.py)
from _llm_mock import MappingLLM
from keymatch import align_keys, apply_mapping, align_values
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


def test_apply_mapping_remapped_flags():
    """apply_mapping метит переименованные ключи флагами remapped/remap_locked (plan §3.6в)."""
    reqs = [
        {"key": "конструкция", "operator": "eq", "value": "На рукоятке"},   # не critical → смягчаемо
        {"key": "источник_света", "operator": "eq", "value": "галоген"},    # critical → заперто
        {"key": "тип", "operator": "eq", "value": "прямой"},                 # без маппинга
    ]
    mapping = {"конструкция": "материал_рукояти", "источник_света": "тип_освещения"}
    out = apply_mapping(reqs, mapping, critical=["источник_света", "количество_апертур"])
    r = []
    r.append(check("remapped=True у переименованного", out[0].get("remapped") is True))
    r.append(check("не-critical ключ НЕ заперт", out[0].get("remap_locked") is False))
    r.append(check("critical ключ заперт (remap_locked=True)", out[1].get("remap_locked") is True))
    r.append(check("непереименованный без флага remapped", out[2].get("remapped") is None))
    r.append(check("исходный список не мутирован", "remapped" not in reqs[0]))
    return r


def test_align_keys_deterministic_no_llm():
    """Детерминированный слой: морфология имени (ё/е) + словарь синонимов сводят ключи
    БЕЗ вызова LLM (когда весь остаток закрыт)."""
    req = {"объем_памяти_гб": 8, "разрешение_видеокамеры": "1280x720"}
    card = {"объём_памяти_гб": 32, "разрешение_камеры": "1280 x 720"}
    syn = [["разрешение_камеры", "разрешение_видеокамеры"]]
    llm = MappingLLM([])
    mp = align_keys(req, card, client=llm, key_synonyms=syn)
    r = []
    r.append(check("морфология ё/е: объем↔объём", mp.get("объем_памяти_гб") == "объём_памяти_гб"))
    r.append(check("словарь: видеокамеры↔камеры", mp.get("разрешение_видеокамеры") == "разрешение_камеры"))
    r.append(check("LLM не вызван (остаток пуст)", llm.calls == 0))
    return r


def test_align_keys_remainder_goes_to_llm():
    """Что не свёл детерминированный слой — уходит в LLM; детерм. пары сохраняются."""
    req = {"объем_памяти_гб": 8, "загадочное_поле": "x"}
    card = {"объём_памяти_гб": 32, "нечто": "y"}
    llm = MappingLLM([{"tz_key": "загадочное_поле", "card_key": "нечто"}])
    mp = align_keys(req, card, client=llm)
    r = []
    r.append(check("детерм. пара сохранена", mp.get("объем_памяти_гб") == "объём_памяти_гб"))
    r.append(check("остаток сведён LLM", mp.get("загадочное_поле") == "нечто"))
    r.append(check("ровно один вызов LLM", llm.calls == 1))
    return r


def test_align_keys_type_guard():
    """align_keys отсекает маппинг с расхождением ТИПА значения: enum «Да» ↔ число 6
    (ложный сменные_апертуры→количество_апертур), но пропускает текст↔текст и число↔число."""
    req_fields = {
        "сменные_апертуры": "Да",          # текст-enum
        "конструкция": "На рукоятке",       # текст
        "напряжение": "3,5 В",              # число (с цифрой)
        "размер": "№3",                     # число
    }
    product_fields = {
        "количество_апертур": 6,            # число  → тип≠ у «Да» → ДОЛЖЕН отсеяться
        "материал_рукояти": "металл",       # текст  → совместимо
        "рабочее_напряжение": "3.5",        # число  → совместимо
        "размеры": ["0", "1", "3", "4"],    # список → тип не проверяем → совместимо
    }
    llm = MappingLLM([
        {"tz_key": "сменные_апертуры", "card_key": "количество_апертур"},  # тип-конфликт
        {"tz_key": "конструкция", "card_key": "материал_рукояти"},         # ок
        {"tz_key": "напряжение", "card_key": "рабочее_напряжение"},        # ок
        {"tz_key": "размер", "card_key": "размеры"},                       # список — ок
    ])
    mp = align_keys(req_fields, product_fields, client=llm)
    r = []
    r.append(check("тип-конфликт (Да↔6) отсечён", "сменные_апертуры" not in mp))
    r.append(check("текст↔текст пропущен", mp.get("конструкция") == "материал_рукояти"))
    r.append(check("число↔число пропущено", mp.get("напряжение") == "рабочее_напряжение"))
    r.append(check("скаляр↔список пропущен (тип не проверяем)", mp.get("размер") == "размеры"))
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
    r = (test_apply_mapping() + test_apply_mapping_remapped_flags()
         + test_align_keys_deterministic_no_llm() + test_align_keys_remainder_goes_to_llm()
         + test_align_keys_type_guard() + test_align_values())
    passed = sum(r)
    print("\n%d/%d passed" % (passed, len(r)))
    sys.exit(0 if passed == len(r) else 1)


if __name__ == "__main__":
    main()
