"""Тесты extractor: детерминированный словарь канонических ключей (_canonical_keys)
и инъекция контролируемого словаря в промпт (с моком LLM, без сети).

Цель — стабильность имён ключей: тот же ТЗ должен давать те же ключи между прогонами.
Sampling-детерминизм (temperature=0) на Sonnet 5 / Opus 4.8 недоступен — параметр удалён,
даёт 400 (см. claude-api). Поэтому рычаг повторяемости — контролируемый словарь имён."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from _llm_mock import ReqsLLM as FakeLLM  # общий мок anthropic-клиента (tests/_llm_mock.py)
from extractor import _canonical_keys, extract_requirements


def check(name, ok):
    print(("  ✓ " if ok else "  ✗ ") + name)
    return ok


PROFILE = {
    "category": "Перчатки медицинские",
    "synonyms": {"нитрил": ["нитриловый", "nitrile"]},   # варианты ЗНАЧЕНИЙ, не имена ключей
    "critical_attributes": ["материал", "стерильность", "опудренность"],
    "required_documents": ["рег_удостоверение"],
}


def test_canonical_keys():
    r = []
    keys = _canonical_keys(PROFILE)
    r.append(check("объединяет critical_attributes и required_documents",
                   set(keys) == {"материал", "стерильность", "опудренность", "рег_удостоверение"}))
    r.append(check("отсортировано (детерминированный порядок)", keys == sorted(keys)))
    r.append(check("НЕ включает ключи synonyms (это значения, не имена полей)",
                   "нитрил" not in keys))
    r.append(check("пустой профиль → пустой список", _canonical_keys({}) == []))
    r.append(check("None-поля не роняют", _canonical_keys(
        {"critical_attributes": None, "required_documents": None}) == []))
    return r


def test_prompt_injection():
    r = []

    llm = FakeLLM()
    reqs = extract_requirements("материал: нитрил", profile=PROFILE, client=llm)
    r.append(check("вернул requirements из ответа модели", reqs and reqs[0]["key"] == "материал"))
    r.append(check("ровно один вызов API", llm.calls == 1))
    r.append(check("текст ТЗ ушёл в промпт", "материал: нитрил" in llm.last_user))
    r.append(check("блок канонических имён в промпте", "канонические_имена_ключей" in llm.last_user))
    r.append(check("каждое каноническое имя присутствует в промпте",
                   all(k in llm.last_user for k in _canonical_keys(PROFILE))))
    r.append(check("твёрдая инструкция «ДОСЛОВНО»", "ДОСЛОВНО" in llm.last_user))

    # без профиля — не падает, канонический блок отсутствует
    llm2 = FakeLLM()
    extract_requirements("любой текст", client=llm2)
    r.append(check("без профиля вызов проходит", llm2.calls == 1))
    r.append(check("без профиля нет канонического блока",
                   "канонические_имена_ключей" not in llm2.last_user))
    return r


def test_position_scoping():
    """Скоуп на позицию многолота (§3.4a): имя позиции и «others» уходят в промпт
    с инструкцией не включать чужие позиции; без position — блока нет."""
    r = []
    pos = {"name": "Клинок для ларингоскопа одноразовый",
           "others": ["Рукоятка многоразовая", "Клинок Миллер"]}

    llm = FakeLLM()
    extract_requirements("текст лота", profile=PROFILE, position=pos, client=llm)
    r.append(check("маркер сборного лота в промпте", "СБОРНЫЙ ЛОТ" in llm.last_user))
    r.append(check("имя позиции товара в промпте", pos["name"] in llm.last_user))
    r.append(check("чужие позиции перечислены (исключить)",
                   all(o in llm.last_user for o in pos["others"])))

    # position=None и пустое имя — как одиночный лот, без блока
    llm2 = FakeLLM()
    extract_requirements("текст", profile=PROFILE, position=None, client=llm2)
    r.append(check("без позиции нет блока лота", "СБОРНЫЙ ЛОТ" not in llm2.last_user))
    llm3 = FakeLLM()
    extract_requirements("текст", position={"name": ""}, client=llm3)
    r.append(check("пустое имя позиции игнорируется", "СБОРНЫЙ ЛОТ" not in llm3.last_user))
    return r


def main():
    r = test_canonical_keys() + test_prompt_injection() + test_position_scoping()
    passed = sum(r)
    print("\n%d/%d passed" % (passed, len(r)))
    sys.exit(0 if passed == len(r) else 1)


if __name__ == "__main__":
    main()
