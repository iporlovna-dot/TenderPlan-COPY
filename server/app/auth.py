"""Пароли (bcrypt) и сессии по cookie для личных кабинетов."""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Response

SESSION_COOKIE = "lekalo_session"
SESSION_DAYS = 30


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=SESSION_DAYS)
    # заодно подметаем протухшие сессии — иначе таблица растёт вечно
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(),))
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user_id, now.isoformat(), expires.isoformat()),
    )
    conn.commit()
    return token


def set_session_cookie(response: Response, token: str) -> None:
    # secure=True: сайт теперь на HTTPS (185.11.134.80.nip.io, Let's Encrypt).
    # ВАЖНО: голый IP по-прежнему живёт на голом HTTP (для старых ссылок) —
    # secure-cookie там браузер не сохранит, т.е. вход/регистрация работают
    # только через https://185.11.134.80.nip.io/, не через голый IP.
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def get_user_by_token(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    return conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token = ? AND s.expires_at > ?",
        (token, datetime.now(timezone.utc).isoformat()),
    ).fetchone()
