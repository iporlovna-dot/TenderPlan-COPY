"""Подключение к БД: движок, сессия, декларативная база, зависимость get_db."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from api.config import settings

# SQLite для MVP; check_same_thread=False — для многопоточного FastAPI-воркера.
_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI-зависимость: сессия на запрос, закрывается по завершении."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from api import models  # noqa: F401 — регистрируем таблицы
    Base.metadata.create_all(bind=engine)
