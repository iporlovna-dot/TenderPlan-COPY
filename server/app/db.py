"""SQLite-хранилище аккаунтов: компании, пользователи, сессии, избранное, история сверок ТЗ.

Файл БД — server/data/lekalo.db (не коммитится, см. .gitignore: содержит хэши паролей).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DB_PATH = os.getenv("LK_DB_PATH", str(Path(__file__).resolve().parent.parent / "data" / "lekalo.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    inn TEXT,
    plan TEXT NOT NULL DEFAULT 'business',
    plan_expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'employee',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS saved_purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purchase_id TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, purchase_id)
);
CREATE TABLE IF NOT EXISTS tz_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purchase_id TEXT,
    purchase_number TEXT,
    purchase_title TEXT,
    score INTEGER,
    verdict TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS searches (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    query TEXT NOT NULL DEFAULT '',
    minus TEXT NOT NULL DEFAULT '',
    filters TEXT NOT NULL DEFAULT '{}',
    new_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# (таблица, [(колонка, DDL-тип-и-дефолт), ...]) — добавляются, если ещё нет.
# ALTER TABLE ADD COLUMN в SQLite не умеет "IF NOT EXISTS", поэтому проверяем
# через PRAGMA table_info сами. Нужно, т.к. в проде уже есть реальные компании —
# пересоздавать таблицу с нуля нельзя.
MIGRATIONS = [
    ("users", [
        ("totp_secret", "TEXT"),
        ("totp_enabled", "INTEGER NOT NULL DEFAULT 0"),
    ]),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_db() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()
