"""Единая схема закупки — та же форма, что ждёт фронт (site/js).

Источники (Портал поставщиков, позже ТЭК-Торг/РТС/Фабрикант/Газпром) нормализуют
свои ответы в эти модели. Ядро/фронт от конкретного источника не зависят."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Lot(BaseModel):
    name: str
    qty: str = "—"
    price: float = 0.0
    okpd: str = ""


class Document(BaseModel):
    id: str
    name: str
    url: str  # прямая ссылка на скачивание через наш прокси /api/documents/{id}


class Check(BaseModel):
    req: str
    status: str  # pass | gap | fail
    note: str | None = None


class MatchResult(BaseModel):
    score: int = 0
    verdict: str = "eligible_with_gaps"  # eligible | eligible_with_gaps | disqualified
    checks: list[Check] = Field(default_factory=list)
    explanation: str = ""


class Purchase(BaseModel):
    id: str                     # напр. "pp_10256395"
    number: str
    title: str
    customer: str = "—"
    customerInn: str = ""
    law: str = "44-ФЗ"          # 44-ФЗ | 223-ФЗ | ...
    source: str = "Портал поставщиков (mos.ru)"
    region: str = ""
    okpd: str = ""
    price: float = 0.0          # НМЦК
    stage: str = "active"       # active | committee | completed
    deadlineDays: int = 0
    publishedDaysAgo: int = 0
    guaranteeApp: float = 0.0
    guaranteeContract: float = 0.0
    prepayment: float = 0.0
    href: str = ""
    lots: list[Lot] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)
    # ключ = product_id, значение = MatchResult (надстройка «сверка по ТЗ»)
    matches: dict[str, MatchResult] = Field(default_factory=dict)


class PurchasePage(BaseModel):
    total: int
    generatedAt: str
    source: str
    purchases: list[Purchase]
