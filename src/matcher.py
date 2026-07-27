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

# Поставочные/документарные поля есть у ЛЮБОГО медизделия (рег. удостоверение, срок
# годности, новизна, сертификаты/декларации, инструкция и язык маркировки, разрешение к
# применению в РФ, гарантия). Совпадение по ним НЕ означает «это наш товар» — они не несут
# категорийного сигнала. Помечаем их DOCUMENTARY (меньший вес) и не считаем за «техническое
# покрытие» позиции в lot_coverage — иначе позиция из одних бумажных полей ложно «покрывается»
# любым товаром с оформленными документами (см. баг ранжирования малых позиций, plan §3).
_SUPPLY_FIELD_RE = re.compile(
    r"рег.*удостовер|ру_имеется|срок_годн|новизн|документ.*соответств|сертификат|"
    r"деклараци|инструкц.*(рус|язык)|маркировк.*рус|язык_маркиров|разреш.*рф|гаранти",
    re.IGNORECASE)


def field_kind(key: str) -> ReqType:
    """Тип требования по ИМЕНИ поля: поставочные/бумажные → DOCUMENTARY, иначе TECHNICAL.
    Детерминированно, без LLM. Используется построителями требований (lot_coverage,
    extractor), чтобы совпадение по общим поставочным полям не завышало покрытие/скоринг."""
    return ReqType.DOCUMENTARY if _SUPPLY_FIELD_RE.search(str(key)) else ReqType.TECHNICAL


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
# разделители размерности («640 x 480», «87 х 20 х 62») — НЕ единица, а знак умножения:
# после такого токена снова идёт число. Иначе «x» из разрешения ложно читается единицей.
_DIM_SEP = {"x", "х", "×", "*"}


def _num_unit(v: object) -> tuple:
    """Достаёт (число, единицу) из значения: «2,5 В» -> (2.5, 'b'), 50000 -> (50000.0, '').

    Единица — буквенный токен СРАЗУ за числом (В, мм, дптр, %), свёрнутый через
    гомоглифы (В↔B, А↔A), чтобы «В»/«в», «мм»/«MM» сравнивались одинаково. Берём токен
    после числа, а не все буквы строки — иначе компаундные значения («1-4 дптр: 1; 4-10
    дптр: 2») дают мусорную единицу. Токен-разделитель размерности («x»/«х»/«×» в «640 x 480»)
    единицей НЕ считаем — иначе разрешение/габариты дают ложную несовместимость единиц.
    Нет токена сразу за числом → единица пустая (мягко).
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
        unit = _fold(um.group(1)) if um else ""
        if unit in _DIM_SEP:
            unit = ""  # «640 x 480» — «x» это знак умножения, не единица измерения
        return float(m.group(0).replace(",", ".")), unit
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


def _req_raw_unit(req: Requirement) -> str:
    """СЫРАЯ единица требования (не свёрнутая): для таблицы семейств единиц (_reconcile)."""
    if req.unit:
        return req.unit
    return _num_and_raw(req.value)[1]


# Семейства единиц: {очищенная_единица: (семейство, коэффициент к базовой)}. Позволяет
# сверять значения разного масштаба одной физвеличины: 1350 мАч == 1.35 А·ч, 130 мм == 13 см.
# Работает на СЫРОЙ единице (до свёртки гомоглифов) — ключи чистим отдельно (_clean_unit).
_UNIT_SCALE = {
    # заряд аккумулятора → база А·ч
    "амперчас": ("charge", 1.0), "ач": ("charge", 1.0), "ah": ("charge", 1.0),
    "миллиамперчас": ("charge", 0.001), "мач": ("charge", 0.001), "mah": ("charge", 0.001),
    # длина → база мм
    "миллиметр": ("len", 1.0), "мм": ("len", 1.0), "mm": ("len", 1.0),
    "сантиметр": ("len", 10.0), "см": ("len", 10.0), "cm": ("len", 10.0),
    "метр": ("len", 1000.0), "м": ("len", 1000.0), "m": ("len", 1000.0),
}


def _clean_unit(u: object) -> str:
    """Единицу к канону для таблицы семейств: lower, ё→е, снять разделители/скобки."""
    return re.sub(r"[^0-9a-zа-я]", "", str(u).lower().replace("ё", "е"))


def _unit_scale(raw_unit: object):
    """(семейство, коэффициент к базовой) для единицы или None (единица пустая/неизвестна)."""
    return _UNIT_SCALE.get(_clean_unit(raw_unit)) if raw_unit else None


# сырая единица: буквенный токен, при необходимости соединённый точкой/интерпунктом
# («А·ч», «Н·м») — иначе «А·ч» обрезалось бы до «А» и не попадало в семейство заряда.
_RAW_UNIT_RE = re.compile(r"\s*((?:[^\W\d_]+)(?:[·.⋅‧][^\W\d_]+)*|%|°)")


def _num_and_raw(v: object) -> tuple:
    """Как _num_unit, но единица — СЫРАЯ (не свёрнутая гомоглифами): нужна для таблицы семейств."""
    if isinstance(v, bool):
        return None, ""
    if isinstance(v, (int, float)):
        return float(v), ""
    if isinstance(v, str):
        s = v.replace("\xa0", " ")
        m = _NUM_RE.search(s)
        if not m:
            return None, ""
        um = _RAW_UNIT_RE.match(s, m.end())
        raw = um.group(1) if um else ""
        if _fold(raw) in _DIM_SEP:
            raw = ""
        return float(m.group(0).replace(",", ".")), raw
    return None, ""


def _reconcile(pv: float, au_raw: object, ru_raw: object) -> tuple:
    """Согласовать единицы товара (au_raw) и требования (ru_raw). -> (pv_в_единице_требования,
    совместимы?). Одно семейство разного масштаба → конвертируем pv (1350 мАч → 1.35 А·ч).
    Иначе — прежняя строгая проверка совместимости на свёрнутых единицах («В»≠«А»)."""
    sa, sr = _unit_scale(au_raw), _unit_scale(ru_raw)
    if sa and sr and sa[0] == sr[0]:
        return pv * sa[1] / sr[1], True
    return pv, _units_compatible(_fold(au_raw), _fold(ru_raw))


# Кириллица → латиница для визуально одинаковых букв. Размеры/коды в ТЗ пишут вперемешку
# («М» кир. ≡ «M» лат., «С»≡«C», «Х»≡«X») — без этого набор размеров ложно не сходится.
_HOMOGLYPHS = str.maketrans({
    "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o",
    "р": "p", "с": "c", "т": "t", "у": "y", "х": "x",
})


# ведущий маркер нумерации размера («№ 3», «# 3», «No.3», «размер 3», «size 3»)
# — паразитный префикс, сам размер это то, что после него: снимаем перед свёрткой.
_SIZE_PREFIX_RE = re.compile(r"^(?:№|#|no\.?|размер|size)\s*", re.IGNORECASE)


def _fold(v: object) -> str:
    """Нормализация для коротких токенов-кодов (размеры, наборы): снимаем ведущий
    маркер нумерации размера («№ 3»→«3») + сведение гомоглифов."""
    return _SIZE_PREFIX_RE.sub("", _norm_str(v)).translate(_HOMOGLYPHS)


def _as_list(v: object) -> list:
    return v if isinstance(v, (list, tuple)) else [v]


# значения-отрицания: наличие такого = «характеристики нет»
_NEG_VALUES = {"нет", "no", "false", "отсутствует", "отсутствие", "0", "-", "—"}


def _is_present(v: object) -> bool:
    """Значение подтверждает наличие характеристики? Непустое и не отрицающее.
    (attr пустой/None отсекается раньше — сюда приходит непустое значение.)"""
    if isinstance(v, bool):
        return v
    return _norm_str(v) not in _NEG_VALUES


_RESOLUTION_RE = re.compile(r"\d\s*[xх×*]\s*\d")


def _resolution_product(v: object) -> Optional[float]:
    """Для разрешения «A x B» (ровно два числа через знак умножения) — произведение A*B
    (число пикселей). Иначе None. ТЗ пишет разрешение то размерами («1280 x 720»), то числом
    пикселей (921600) — движок должен принимать обе формы. Ровно 2 числа: габариты «AxBxC»
    (3 числа) сюда НЕ попадают, чтобы не давать ложное произведение."""
    if not isinstance(v, str):
        return None
    s = v.replace("\xa0", " ").lower()
    nums = _NUM_RE.findall(s)
    if len(nums) != 2 or not _RESOLUTION_RE.search(s):
        return None
    return float(nums[0].replace(",", ".")) * float(nums[1].replace(",", "."))


def evaluate(req: Requirement, attr: Optional[Attribute],
             synonyms: Optional[dict] = None) -> Check:
    """Проверяет одно требование против характеристики товара."""
    # Нет данных в карточке → пробел (не нарушение!). Это ценность продукта.
    if attr is None or attr.value in (None, "", []):
        action = _gap_action(req)
        return Check(req, Status.GAP, note="нет данных в карточке", action=action)

    op = req.operator

    if op == Operator.EQ:
        # требование-«наличие» пришло булевым (extractor кодирует «Наличие»/«должен быть X»
        # как true) — это present-семантика, а не строгое равенство: карточка со ЛЮБЫМ
        # осмысленным (не отрицающим) значением удовлетворяет. Иначе «новый…»≠«true» → ложь.
        if isinstance(req.value, bool):
            ok = req.value == _is_present(attr.value)
            return _pass_or_violation(req, ok, attr)
        # ТЗ и товар оба задают НАБОР (список требуемых клинков ↔ ассортимент товара) —
        # это покрытие набора, а не строгое равенство списков: непокрытое → пробел, НЕ нарушение
        if isinstance(req.value, (list, tuple)) and isinstance(attr.value, (list, tuple)):
            missing = [x for x in req.value
                       if not _val_match(x, list(attr.value), synonyms)]
            if not missing:
                return _pass_or_violation(req, True, attr)
            joined = ", ".join(str(x) for x in missing)
            return Check(req, Status.GAP, note="не покрыт набор: " + joined,
                         action="подтвердить наличие: " + joined)
        # разрешение: «1280 x 720» ↔ 921600 (пиксели) ↔ «1280x720» — принимаем обе формы
        rpa = _resolution_product(attr.value)
        if rpa is not None:
            rpr = _resolution_product(req.value)
            target = rpr if rpr is not None else _to_number(req.value)
            if target is not None and abs(rpa - target) <= 1e-6 * max(1.0, rpa):
                return _pass_or_violation(req, True, attr)
        # товар доступен в НАБОРЕ значений (размеры ['0'..'4']), а ТЗ просит одно («№3») —
        # проходит, если запрошенное входит в набор (иначе eq сравнил бы весь список строкой)
        if isinstance(attr.value, (list, tuple)):
            ok = any(_val_match(req.value, [x], synonyms) or _eq(x, req.value, synonyms)
                     for x in attr.value)
            return _pass_or_violation(req, ok, attr)
        num = _eq_numeric(attr.value, req)     # «2,5 В» vs 2.5/«В»: число+единица
        ok = num if num is not None else _eq(attr.value, req.value, synonyms)
        return _pass_or_violation(req, ok, attr)

    if op in (Operator.GTE, Operator.LTE):
        pv, au = _num_and_raw(attr.value)
        rv = _to_number(req.value)
        if pv is None or rv is None:
            return Check(req, Status.GAP, note="значение не распознано как число")
        pv, compat = _reconcile(pv, au, _req_raw_unit(req))  # 1350 мАч → 1.35 А·ч
        if not compat:
            return _pass_or_violation(req, False, attr)   # напр. «3 А» ≠ «≥2,5 В»
        ok = pv >= rv if op == Operator.GTE else pv <= rv
        if not ok:  # разрешение «1280 x 720» как число пикселей (921600)
            rp = _resolution_product(attr.value)
            if rp is not None:
                ok = rp >= rv if op == Operator.GTE else rp <= rv
        note = "проходит впритык" if ok and pv == rv else ""
        return _pass_or_violation(req, ok, attr, note=note)

    if op == Operator.RANGE:
        pv, au = _num_and_raw(attr.value)
        lo, hi = _to_number(req.value[0]), _to_number(req.value[1])
        if pv is None:
            return _pass_or_violation(req, False, attr)
        pv, compat = _reconcile(pv, au, _req_raw_unit(req))
        ok = compat and lo <= pv <= hi
        if not ok:  # разрешение как число пикселей
            rp = _resolution_product(attr.value)
            if rp is not None:
                ok = lo <= rp <= hi
        return _pass_or_violation(req, ok, attr)

    if op == Operator.ONE_OF:
        # значение товара должно попасть в допустимый набор ТЗ (гомоглифы + синонимы)
        allowed = _as_list(req.value)
        ok = any(_val_match(pv, allowed, synonyms) for pv in _as_list(attr.value))
        return _pass_or_violation(req, ok, attr)

    if op == Operator.SET:
        # товар должен покрывать ВЕСЬ требуемый набор (напр. размеры S,M,L)
        prod_vals = _as_list(attr.value)
        # недостающие показываем в исходном регистре, а не нормализованными
        missing = [x for x in _as_list(req.value) if not _val_match(x, prod_vals, synonyms)]
        if not missing:
            return _pass_or_violation(req, True, attr)
        joined = ", ".join(str(x) for x in missing)
        return Check(req, Status.GAP, note="не покрыт набор: " + joined,
                     action="подтвердить наличие: " + joined)

    if op == Operator.PRESENT:
        return _pass_or_violation(req, True, attr)

    return Check(req, Status.GAP, note="неизвестный оператор")


def _val_match(pv: object, allowed: list, synonyms: Optional[dict]) -> bool:
    """Значение `pv` совпадает с одним из `allowed`: по свёртке гомоглифов (М кир↔M лат,
    для размеров/кодов) ИЛИ по синонимам категории (LED↔«светодиодная лампа»). Нужна для
    one_of/set — раньше они матчили только строгой свёрткой и не знали синонимов."""
    pf = _fold(pv)
    if any(pf == _fold(a) for a in allowed):
        return True
    if synonyms:
        pn = _norm_str(pv)
        for a in allowed:
            an = _norm_str(a)
            for canon, alts in synonyms.items():
                group = {_norm_str(canon)} | {_norm_str(x) for x in alts}
                if pn in group and an in group:
                    return True
    return False


def _multi_number(v: object) -> bool:
    """В значении больше одного числа? («48-50/170-176», «170-176»). Такие КОМПАУНДЫ
    числовой eq сверять нельзя — прочитали бы только первое число и дали ложь; их
    отдаём строковой _eq (подстрока)."""
    return len(_NUM_RE.findall(str(v))) > 1


def _eq_numeric(attr_value: object, req: Requirement) -> Optional[bool]:
    """Числовая сверка eq с учётом единицы. None — если не оба значения числовые ИЛИ
    хотя бы одно компаундное (несколько чисел) — тогда решает строковая _eq. Иначе:
    число совпало И единица совместима. «2,5 В»↔2.5/«В» → True; «2,5 А»↔2.5/«В» → False."""
    if _multi_number(attr_value) or _multi_number(req.value):
        return None  # компаунд («48-50/170-176») → строковая сверка, не первое число
    an, au = _num_and_raw(attr_value)
    rn, _ru = _num_and_raw(req.value)
    if an is None or rn is None:
        return None
    an, compat = _reconcile(an, au, _req_raw_unit(req))  # масштаб: 1.35 А·ч ↔ 1350 мАч
    if not compat:
        return False
    return abs(an - rn) <= 1e-9 * max(1.0, abs(rn))  # допуск на float-погрешность конверсии


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
    # Ключ сопоставлен семантикой (align_keys), а не дословно — маппинг мог ошибиться.
    # Неверный маппинг НЕ должен дисквалифицировать: несоответствие по remapped-ключу
    # (кроме critical_attributes — там маппинг заперт) трактуем как пробел, не нарушение.
    # «Неверный маппинг хуже отсутствующего»: пробел безопаснее ложной дисквалификации (plan §3.6в).
    if req.remapped and not req.remap_locked:
        return Check(req, Status.GAP,
                     note="семантическое сопоставление ключа не подтвердилось (товар: %s) — "
                          "трактуем как пробел, не нарушение" % attr.value,
                     action=_gap_action(req))
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
