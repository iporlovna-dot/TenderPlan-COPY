"""Модели данных движка сопоставления.

Схемы категория-независимы: ключи характеристик произвольные, движок
не знает про перчатки. См. plan.md §5, §7A.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Union

Number = Union[int, float]


class Operator(str, Enum):
    """Как сравнивать значение требования со значением товара."""
    EQ = "eq"            # точное равенство (строка/число)
    GTE = "gte"          # не менее (≥)
    LTE = "lte"          # не более (≤)
    RANGE = "range"      # диапазон [min, max]
    ONE_OF = "one_of"    # значение товара входит в допустимый набор ТЗ
    SET = "set"          # товар покрывает весь набор (напр. размеры S,M,L)
    PRESENT = "present"  # характеристика/документ просто должен быть


class Hardness(str, Enum):
    HARD = "hard"        # «значение не может изменяться» → дисквалификатор
    SOFT = "soft"        # «участник указывает конкретное значение»


class ReqType(str, Enum):
    TECHNICAL = "technical"      # характеристика товара
    DOCUMENTARY = "documentary"  # документ/условие (РУ, срок годности, новизна)


class Status(str, Enum):
    PASS = "pass"          # проходит
    VIOLATION = "violation"  # нарушение
    GAP = "gap"            # нет данных в карточке / нужно подтвердить


class Verdict(str, Enum):
    ELIGIBLE = "eligible"
    ELIGIBLE_WITH_GAPS = "eligible_with_gaps"
    DISQUALIFIED = "disqualified"


@dataclass
class Attribute:
    """Характеристика товара в карточке поставщика."""
    key: str
    value: object                      # str | number | list
    status: str = "declared"           # declared | confirmable | gap
    doc: Optional[str] = None          # чем подтверждается (для confirmable)


@dataclass
class Product:
    id: str
    name: str
    attributes: List[Attribute] = field(default_factory=list)

    def get(self, key: str) -> Optional[Attribute]:
        for a in self.attributes:
            if a.key == key:
                return a
        return None


@dataclass
class Requirement:
    """Извлечённое из ТЗ требование закупки."""
    key: str
    operator: Operator
    value: object = None               # число, строка, [min,max] или [набор]
    unit: Optional[str] = None
    hardness: Hardness = Hardness.SOFT
    type: ReqType = ReqType.TECHNICAL
    raw: str = ""                      # исходная формулировка из ТЗ


@dataclass
class Check:
    """Результат проверки одного требования."""
    req: Requirement
    status: Status
    note: str = ""
    action: str = ""                   # что сделать поставщику (для gap)


@dataclass
class MatchResult:
    purchase_id: str
    product_id: str
    score: int
    verdict: Verdict
    checks: List[Check] = field(default_factory=list)
    explanation: str = ""


@dataclass
class Purchase:
    """Сырая карточка закупки из ЕИС (шаг 1). Вложения ТЗ качаются отдельно (шаг 4)."""
    id: str                                # реестровый номер закупки
    subject: str = ""                      # предмет закупки (название)
    okpd2: List[str] = field(default_factory=list)   # коды ОКПД2
    ktru: List[str] = field(default_factory=list)     # коды КТРУ
    customer: str = ""                     # заказчик
    price: Optional[float] = None          # НМЦК
    attachments: List[str] = field(default_factory=list)  # ссылки на вложения ТЗ
    law: str = ""                          # 44-ФЗ | 223-ФЗ
    region: Optional[str] = None           # код региона (напр. "77" — Москва) — фильтр воронки
    submission_close: Optional[int] = None  # дедлайн подачи заявок, epoch ms — фильтр воронки
