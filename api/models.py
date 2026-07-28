"""Модели БД: мультиарендность через company_id (изоляция на уровне данных, не в ядре).

Company 1—* User, Company 1—* Product, Company 1—* Lead. Каждая строка данных несёт
company_id; все запросы фильтруются по компании текущего пользователя — чужое не видно.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    users: Mapped[list["User"]] = relationship(back_populates="company")
    products: Mapped[list["Product"]] = relationship(back_populates="company")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="owner")
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    company: Mapped[Company] = relationship(back_populates="users")


class Product(Base):
    """Карточка товара поставщика (тот же формат, что в data/products/*.json)."""
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    product_key: Mapped[str] = mapped_column(String(120), nullable=False)  # id карточки (для ingest)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    ktru_json: Mapped[str] = mapped_column(Text, default="[]")
    attributes_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    company: Mapped[Company] = relationship(back_populates="products")
    __table_args__ = (UniqueConstraint("company_id", "product_key", name="uq_company_productkey"),)


class Lead(Base):
    """Вердикт по закупке под товар (лента). Заполняется ingest'ом из run_auto --out."""
    __tablename__ = "leads"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    purchase_id: Mapped[str] = mapped_column(String(80), nullable=False)
    subject: Mapped[str] = mapped_column(Text, default="")
    customer: Mapped[str] = mapped_column(String(300), default="")
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    submission_close: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # epoch ms
    url: Mapped[str] = mapped_column(String(300), default="")
    score: Mapped[int] = mapped_column(Integer, default=0)
    verdict: Mapped[str] = mapped_column(String(30), default="")
    explanation: Mapped[str] = mapped_column(Text, default="")
    # Замороженные требования закупки (+синонимы профиля) — нужны для пересчёта % при
    # дозаполнении пробелов клиентом (§Этап 1, фича founder). Пишет ingest из run_auto.
    requirements_json: Mapped[str] = mapped_column(Text, default="[]")
    synonyms_json: Mapped[str] = mapped_column(Text, default="{}")
    # наглядная карточка контракта (даты, обеспечения, аванс, ссылка) — §Этап 1
    card_json: Mapped[str] = mapped_column(Text, default="{}")
    # версионность §6: детект изменения ТЗ между прогонами
    content_hash: Mapped[str] = mapped_column(String(32), default="")          # отпечаток требований
    source_updated_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # метка источника
    change_status: Mapped[str] = mapped_column(String(12), default="unchanged")  # unchanged|formal|material
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (UniqueConstraint("product_id", "purchase_id", name="uq_product_purchase"),)
