"""Pydantic-схемы запросов/ответов API."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    company_name: str = Field(min_length=1, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AttributeIn(BaseModel):
    key: str
    value: object = None
    status: str = "declared"
    doc: Optional[str] = None


class ProductIn(BaseModel):
    product_key: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=500)
    ktru: List[str] = Field(default_factory=list)
    attributes: List[AttributeIn] = Field(default_factory=list)


class ProductOut(BaseModel):
    id: int
    product_key: str
    name: str
    ktru: List[str]
    attributes: List[dict]


class ContractCard(BaseModel):
    """Наглядная карточка контракта: даты (epoch ms), деньги (₽), реквизиты (§Этап 1)."""
    reg_number: str = ""
    href: str = ""
    customer: str = ""
    nmck: Optional[float] = None            # НМЦК
    region: Optional[str] = None
    delivery_place: str = ""
    placing_way: str = ""                   # способ определения поставщика
    smp: bool = False                       # только для СМП/СОНКО
    # даты процедуры (epoch ms) — фронт форматирует «до какого числа»
    publication_date: Optional[int] = None  # публикация извещения
    submission_start: Optional[int] = None  # начало приёма заявок
    submission_close: Optional[int] = None  # окончание подачи (срок «до»)
    bidding_date: Optional[int] = None      # проведение торгов/аукциона
    summing_up_date: Optional[int] = None   # подведение итогов
    days_to_submission: Optional[float] = None  # дней до окончания подачи (для наглядности)
    # деньги (₽)
    guarantee_app: Optional[float] = None      # обеспечение заявки
    guarantee_contract: Optional[float] = None  # обеспечение исполнения контракта
    prepayment: Optional[float] = None          # аванс
    # срок исполнения контракта/этапов — НЕ в фиде, живёт в проекте контракта (вложение);
    # заполнится отдельной задачей (парсинг проекта контракта). Пока None.
    execution_period: Optional[str] = None


class LeadOut(BaseModel):
    purchase_id: str
    subject: str
    customer: str
    price: Optional[float]
    region: Optional[str]
    submission_close: Optional[int]
    days_left: Optional[float]
    url: str
    score: int
    verdict: str
    explanation: str
    card: Optional[ContractCard] = None


class CheckOut(BaseModel):
    req: str
    status: str                       # pass | violation | gap
    note: str = ""
    action: str = ""                  # что сделать (для gap) — подсказка клиенту


class LeadDetailOut(BaseModel):
    purchase_id: str
    subject: str
    score: int
    verdict: str
    explanation: str
    checks: List[CheckOut]
    gaps: List[str]                   # ключи требований-пробелов — их можно дозаполнить
    attributes: List[dict]            # текущая карточка товара (с уже внесёнными дозаполнениями)
    card: Optional[ContractCard] = None  # карточка контракта (даты, обеспечения, аванс)


class GapFillIn(BaseModel):
    fills: dict                       # {ключ_пробела: значение_клиента}
