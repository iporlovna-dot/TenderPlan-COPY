"""Тесты парсера срока исполнения контракта (src/contract_terms.py). Синтетика (реальные — с ПДн)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from contract_terms import parse_contract_terms, summary


def check(name, ok):
    print(("  ✓ " if ok else "  ✗ ") + name)
    return ok


def test_duration_with_trigger():
    t = "Срок исполнения контракта: поставка осуществляется в течение 30 (тридцати) календарных " \
        "дней с даты заключения контракта."
    res = parse_contract_terms(t)
    r = []
    r.append(check("длительность распознана", "30" in (res["execution_period"] or "")))
    r.append(check("единица — календарные дни", "календарн" in (res["execution_period"] or "").lower()))
    r.append(check("confidence high (по триггеру)", res["confidence"] == "high"))
    return r


def test_until_date():
    res = parse_contract_terms("Поставка товара осуществляется не позднее 31.12.2026.")
    r = []
    r.append(check("дата-дедлайн распознана", "31.12.2026" in (res["execution_period"] or "")))
    r.append(check("confidence high", res["confidence"] == "high"))
    return r


def test_working_days():
    res = parse_contract_terms("Срок поставки — 15 рабочих дней с момента подписания контракта.")
    return [check("15 рабочих дней", "15" in (res["execution_period"] or "")
                  and "рабоч" in (res["execution_period"] or "").lower())]


def test_stages():
    t = "Товар поставляется поэтапно. Этап 1 — до 01.06.2026. Этап 2 — до 01.12.2026."
    res = parse_contract_terms(t)
    r = []
    r.append(check("распознаны 2 этапа", len(res["stages"]) == 2))
    r.append(check("этап 1 с датой", any("01.06.2026" in s for s in res["stages"])))
    r.append(check("этап 2 с датой", any("01.12.2026" in s for s in res["stages"])))
    r.append(check("summary содержит этапы", "Этап" in (summary(res) or "")))
    return r


def test_no_terms():
    res = parse_contract_terms("Настоящий контракт регулирует отношения сторон по поставке.")
    r = []
    r.append(check("ничего не выдумано", res["execution_period"] is None and not res["stages"]))
    r.append(check("confidence none", res["confidence"] == "none"))
    r.append(check("summary пустой → None", summary(res) is None))
    return r


def test_empty():
    res = parse_contract_terms("")
    return [check("пустой вход → none", res["confidence"] == "none")]


def main():
    r = (test_duration_with_trigger() + test_until_date() + test_working_days()
         + test_stages() + test_no_terms() + test_empty())
    passed = sum(r)
    print("\n%d/%d passed" % (passed, len(r)))
    sys.exit(0 if passed == len(r) else 1)


if __name__ == "__main__":
    main()
