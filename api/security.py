"""Безопасность: хеш паролей (argon2), JWT-токены, rate-limit логина.

Secure by design (plan §5): argon2id для паролей, короткоживущий JWT, лимит неудачных
логинов на (IP+email) с ВРЕМЕННОЙ блокировкой (не вечной) — защита от перебора.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from api.config import settings

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def create_access_token(subject: str, company_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "cid": company_id,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_min),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None


class LoginRateLimiter:
    """Счётчик неудачных логинов на ключ (IP+email). Достиг лимита → временная блокировка.
    In-memory (один процесс) — для MVP; в проде вынести в общий стор (redis)."""

    def __init__(self, max_fails: int, lockout_sec: int):
        self.max_fails = max_fails
        self.lockout_sec = lockout_sec
        self._state: dict[str, tuple[int, float]] = {}  # key -> (fails, locked_until)
        self._lock = threading.Lock()

    def locked_for(self, key: str) -> float:
        """Сколько секунд ещё заблокировано (0 — не заблокировано)."""
        with self._lock:
            _, locked_until = self._state.get(key, (0, 0.0))
            return max(0.0, locked_until - time.time())

    def record_fail(self, key: str) -> None:
        with self._lock:
            fails, _ = self._state.get(key, (0, 0.0))
            fails += 1
            locked_until = time.time() + self.lockout_sec if fails >= self.max_fails else 0.0
            self._state[key] = (fails, locked_until)

    def reset(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)


login_limiter = LoginRateLimiter(settings.login_max_fails, settings.login_lockout_sec)
