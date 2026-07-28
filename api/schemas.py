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
    attributes: List[dict]            # текущая карточка (с уже внесёнными дозаполнениями)


class GapFillIn(BaseModel):
    fills: dict                       # {ключ_пробела: значение_клиента}
