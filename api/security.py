"""Безопасность: хеш паролей (argon2), JWT-токены, rate-limit логина.

Secure by design (plan §5): argon2id для паролей, короткоживущий JWT, лимит неудачных
логинов на (IP+email) с ВРЕМЕННОЙ блокировкой (не вечной) — защита от перебора.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from api.config import settings

_ph = PasswordHasher()


def hash_token(token: str) -> str:
    """SHA-256 refresh-токена: в БД храним только хэш (утечка БД ≠ действующие токены)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_refresh_token() -> Tuple[str, str]:
    """(plaintext, hash) нового refresh-токена. Plaintext уходит клиенту ОДИН раз, в БД — хэш."""
    token = secrets.token_urlsafe(48)
    return token, hash_token(token)


def refresh_expiry() -> datetime:
    """Срок годности refresh-токена (наивный UTC — сравнимо с тем, как SQLite отдаёт datetime)."""
    return datetime.utcnow() + timedelta(days=settings.refresh_token_ttl_days)


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


class RedisLoginRateLimiter:
    """Тот же контракт, но счётчик в Redis — ОБЩИЙ между процессами/воркерами (prod).

    record_fail: INCR ключа неудач с TTL; достиг лимита → ставим lock-ключ на lockout_sec.
    locked_for: TTL lock-ключа. reset: удалить оба ключа."""

    def __init__(self, url: str, max_fails: int, lockout_sec: int):
        import redis  # опциональная зависимость — импорт лениво в конструкторе
        self.r = redis.Redis.from_url(url, socket_connect_timeout=2, decode_responses=True)
        self.r.ping()  # проверка связи сразу — иначе фабрика поймает и деградирует в in-memory
        self.max_fails = max_fails
        self.lockout_sec = lockout_sec

    def locked_for(self, key: str) -> float:
        ttl = self.r.ttl("specmatch:rll:" + key)
        return float(ttl) if ttl and ttl > 0 else 0.0

    def record_fail(self, key: str) -> None:
        fk = "specmatch:rlf:" + key
        n = self.r.incr(fk)
        self.r.expire(fk, self.lockout_sec)
        if n >= self.max_fails:
            self.r.set("specmatch:rll:" + key, 1, ex=self.lockout_sec)

    def reset(self, key: str) -> None:
        self.r.delete("specmatch:rlf:" + key, "specmatch:rll:" + key)


def _make_login_limiter():
    """Redis-стор, если задан SPECMATCH_REDIS_URL и доступен; иначе in-memory (мягкая деградация)."""
    if settings.redis_url:
        try:
            return RedisLoginRateLimiter(settings.redis_url, settings.login_max_fails,
                                         settings.login_lockout_sec)
        except Exception:
            import warnings
            warnings.warn("Redis недоступен — rate-limit логина в in-memory (per-process)")
    return LoginRateLimiter(settings.login_max_fails, settings.login_lockout_sec)


login_limiter = _make_login_limiter()
