"""Детерминированный движок сопоставления товар ↔ требования ТЗ.

Никакого LLM: чистая логика операторов и скоринга (plan.md §6).
LLM отвечает только за извлечение требований (extractor.py) и
семантическую эквивалентность формулировок — сюда приходит уже структура.
"""
from __future__ import annotations

import re
from typing import List, Optional

from schema import (
    Attribute, Check, Hardness, MatchResult, Number, Operator, Product,
    ReqType, Requirement, Status, Verdict,
)

# Вес типов требований в итоговом проценте (технические важнее документарных)
WEIGHTS = {ReqType.TECHNICAL: 2.0, ReqType.DOCUMENTARY: 1.0}


def _to_number(v: object) -> Optional[float]:
    """Достаёт число из значения ('0,11 мм' -> 0.11). Возвращает None, если не число."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:[.,]\d+)?", v.replace("\xa0", " "))
        if m:
            return float(m.group(0).replace(",", "."))
    return None


def _norm_str(v: object) -> str:
    return str(v).strip().lower()


_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
_UNIT_RE = re.compile(r"\s*([^\W\d_]+|%|°)")  # единица — токен СРАЗУ после числа


def _num_unit(v: object) -> tuple:
    """Достаёт (число, единицу) из значения: «2,5 В» -> (2.5, 'b'), 50000 -> (50000.0, '').

    Единица — буквенный токен СРАЗУ за числом (В, мм, дптр, %), свёрнутый через
    гомоглифы (В↔B, А↔A), чтобы «В»/«в», «мм»/«MM» сравнивались одинаково. Берём токен
    после числа, а не все буквы строки — иначе компаундные значения («1-4 дптр: 1; 4-10
    дптр: 2») дают мусорную единицу. Нет токена сразу за числом → единица пустая (мягко).
    (None, '') — если числа нет. bool не число (True/False не путать с 1/0)."""
    if isinstance(v, bool):
        return None, ""
    if isinstance(v, (int, float)):
        return float(v), ""
    if isinstance(v, str):
        s = v.replace("\xa0", " ")
        m = _NUM_RE.search(s)
        if not m:
            return None, ""
        um = _UNIT_RE.match(s, m.end())
        return float(m.group(0).replace(",", ".")), (_fold(um.group(1)) if um else "")
    return None, ""


def _units_compatible(au: str, ru: str) -> bool:
    """Единицы совместимы, если совпадают ИЛИ хотя бы одна не задана (карта/ТЗ часто
    опускают единицу). Заданы и различны — НЕ совместимы: «В» ≠ «А», иначе вольты
    ложно «подтвердятся» амперами."""
    return not (au and ru and au != ru)


def _req_unit(req: Requirement) -> str:
    """Единица требования: явное поле unit (если извлечено), иначе — из строки value."""
    if req.unit:
        return _fold(req.unit)
    return _num_unit(req.value)[1]


# Кириллица → латиница для визуально одинаковых букв. Размеры/коды в ТЗ пишут вперемешку
# («М» кир. ≡ «M» лат., «С»≡«C», «Х»≡«X») — без этого набор размеров ложно не сходится.
_HOMOGLYPHS = str.maketrans({
    "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o",
    "р": "p", "с": "c", "т": "t", "у": "y", "х": "x",
})


def _fold(v: object) -> str:
    """Нормализация для коротких токенов-кодов (размеры, наборы): + сведение гомоглифов."""
    return _norm_str(v).translate(_HOMOGLYPHS)


def _as_list(v: object) -> list:
    return v if isinstance(v, (list, tuple)) else [v]


def evaluate(req: Requirement, attr: Optional[Attribute],
             synonyms: Optional[dict] = None) -> Check:
    """Проверяет одно требование против характеристики товара."""
    # Нет данных в карточке → пробел (не нарушение!). Это ценность продукта.
    if attr is None or attr.value in (None, "", []):
        action = _gap_action(req)
        return Check(req, Status.GAP, note="нет данных в карточке", action=action)

    op = req.operator

    if op == Operator.EQ:
        num = _eq_numeric(attr.value, req)     # «2,5 В» vs 2.5/«В»: число+единица
        ok = num if num is not None else _eq(attr.value, req.value, synonyms)
        return _pass_or_violation(req, ok, attr)

    if op in (Operator.GTE, Operator.LTE):
        pv, au = _num_unit(attr.value)
        rv = _to_number(req.value)
        if pv is None or rv is None:
            return Check(req, Status.GAP, note="значение не распознано как число")
        if not _units_compatible(au, _req_unit(req)):
            return _pass_or_violation(req, False, attr)   # напр. «3 А» ≠ «≥2,5 В»
        ok = pv >= rv if op == Operator.GTE else pv <= rv
        note = "проходит впритык" if ok and pv == rv else ""
        return _pass_or_violation(req, ok, attr, note=note)

    if op == Operator.RANGE:
        pv, au = _num_unit(attr.value)
        lo, hi = _to_number(req.value[0]), _to_number(req.value[1])
        ok = (pv is not None and _units_compatible(au, _req_unit(req))
              and lo <= pv <= hi)
        return _pass_or_violation(req, ok, attr)

    if op == Operator.ONE_OF:
        # значение товара должно попасть в допустимый набор ТЗ (гомоглифы сведены)
        allowed = {_fold(x) for x in _as_list(req.value)}
        prod_vals = {_fold(x) for x in _as_list(attr.value)}
        ok = bool(prod_vals & allowed)
        return _pass_or_violation(req, ok, attr)

    if op == Operator.SET:
        # товар должен покрывать ВЕСЬ требуемый набор (напр. размеры S,M,L)
        provided = {_fold(x) for x in _as_list(attr.value)}
        # недостающие показываем в исходном регистре, а не нормализованными
        missing = [x for x in _as_list(req.value) if _fold(x) not in provided]
        if not missing:
            return _pass_or_violation(req, True, attr)
        joined = ", ".join(str(x) for x in missing)
        return Check(req, Status.GAP, note="не покрыт набор: " + joined,
                     action="подтвердить наличие: " + joined)

    if op == Operator.PRESENT:
        return _pass_or_violation(req, True, attr)

    return Check(req, Status.GAP, note="неизвестный оператор")


def _eq_numeric(attr_value: object, req: Requirement) -> Optional[bool]:
    """Числовая сверка eq с учётом единицы. None — если не оба значения числовые
    (тогда решает строковая _eq). Иначе: число совпало И единица совместима.
    «2,5 В»↔2.5/«В» → True; «2,5 А»↔2.5/«В» → False (единица другая)."""
    an, _au = _num_unit(attr_value)
    rn, _ru = _num_unit(req.value)
    if an is None or rn is None:
        return None
    if an != rn:
        return False
    return _units_compatible(_au, _req_unit(req))


def _eq(pv: object, rv: object, synonyms: Optional[dict]) -> bool:
    a, b = _norm_str(pv), _norm_str(rv)
    if a == b or a in b or b in a:
        return True
    # синонимы категории: {"нитрил": ["нитрильный латекс", ...]}
    if synonyms:
        for canon, alts in synonyms.items():
            group = {_norm_str(canon)} | {_norm_str(x) for x in alts}
            if a in group and b in group:
                return True
    return False


def _pass_or_violation(req: Requirement, ok: bool, attr: Attribute,
                       note: str = "") -> Check:
    if ok:
        n = note
        if attr.status == "confirmable":
            n = (n + "; " if n else "") + "подтвердить документом: " + (attr.doc or "")
        return Check(req, Status.PASS, note=n)
    return Check(req, Status.VIOLATION,
                 note="товар: %s, требуется: %s" % (attr.value, _fmt_req(req)))


def _gap_action(req: Requirement) -> str:
    return "указать/подтвердить: %s (%s)" % (req.key, _fmt_req(req))


def _fmt_req(req: Requirement) -> str:
    sym = {Operator.GTE: "≥", Operator.LTE: "≤", Operator.EQ: "=",
           Operator.RANGE: "диапазон", Operator.ONE_OF: "одно из",
           Operator.SET: "набор", Operator.PRESENT: "наличие"}
    return ("%s %s %s" % (sym.get(req.operator, req.operator), req.value or "",
                          req.unit or "")).strip()


def match(product: Product, requirements: List[Requirement],
          purchase_id: str, synonyms: Optional[dict] = None) -> MatchResult:
    """Полное сопоставление: считает статусы, процент и вердикт."""
    checks = [evaluate(r, product.get(r.key), synonyms) for r in requirements]

    # Дисквалификация: нарушено хоть одно hard-требование
    disqualified = any(c.status == Status.VIOLATION and c.req.hardness == Hardness.HARD
                       for c in checks)

    # Взвешенный процент: pass = полный вес, gap = 0, violation = 0
    total_w = sum(WEIGHTS[c.req.type] for c in checks) or 1.0
    got_w = sum(WEIGHTS[c.req.type] for c in checks if c.status == Status.PASS)
    score = round(got_w / total_w * 100)

    if disqualified:
        verdict = Verdict.DISQUALIFIED
    elif all(c.status == Status.PASS for c in checks):
        verdict = Verdict.ELIGIBLE
    else:
        verdict = Verdict.ELIGIBLE_WITH_GAPS

    return MatchResult(purchase_id, product.id, score, verdict, checks,
                       explanation=_explain(verdict, checks))


def _explain(verdict: Verdict, checks: List[Check]) -> str:
    passed = sum(1 for c in checks if c.status == Status.PASS)
    gaps = [c for c in checks if c.status == Status.GAP]
    viol = [c for c in checks if c.status == Status.VIOLATION]
    if verdict == Verdict.DISQUALIFIED:
        return "Не проходишь: нарушены обязательные требования — " + \
               "; ".join(c.req.key for c in viol)
    base = "Проходишь по %d из %d требований." % (passed, len(checks))
    if gaps:
        base += " Закрой пробелы: " + "; ".join(
            "%s (%s)" % (c.req.key, c.action or c.note) for c in gaps)
    return base
