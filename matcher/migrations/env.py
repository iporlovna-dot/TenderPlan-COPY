"""Alembic env: URL и метаданные берём из приложения (не хардкодим).

target_metadata = Base.metadata (api.db) со всеми моделями (api.models) → autogenerate видит схему.
URL — из settings (env SPECMATCH_DATABASE_URL). Секреты вне кода (secure by design)."""
from __future__ import annotations

import os
import sys

from alembic import context

# корень проекта в путь — чтобы импортировать пакет api
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import models  # noqa: F401,E402 — регистрирует таблицы в Base.metadata
from api.config import settings  # noqa: E402
from api.db import Base  # noqa: E402

target_metadata = Base.metadata


def _url() -> str:
    return settings.database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True,
                      compare_type=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import create_engine
    connect_args = {"check_same_thread": False} if _url().startswith("sqlite") else {}
    engine = create_engine(_url(), connect_args=connect_args, future=True)
    with engine.connect() as connection:
        # render_as_batch — чтобы ALTER TABLE работал на SQLite (он ALTER почти не умеет)
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
