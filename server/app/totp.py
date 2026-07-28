"""Двухфакторная аутентификация (TOTP — Google Authenticator/Authy и т.п.).

Секрет хранится в users.totp_secret, включается только после первого верного
кода (totp_enabled). Второй шаг входа — краткоживущий (5 мин) токен в памяти,
не в БД: он не переживает рестарт процесса, но это ровно то, что нужно —
если сервер перезапустился между вводом пароля и вводом кода, просто входим
заново с начала."""

from __future__ import annotations

import io
import secrets
import threading
import time

import pyotp
import qrcode
import qrcode.image.svg
from fastapi import HTTPException

ISSUER = "Лекало"
PENDING_TTL = 300  # 5 минут на ввод кода после пароля


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def verify_code(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    try:
        return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)
    except Exception:
        return False


def qr_svg(uri: str) -> str:
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


# ---------- краткоживущий токен «пароль верный, ждём код 2FA» ----------

_pending: dict[str, tuple[int, float]] = {}
_lock = threading.Lock()


def _gc(now: float) -> None:
    for tok, (_, exp) in list(_pending.items()):
        if exp < now:
            _pending.pop(tok, None)


def create_pending(user_id: int) -> str:
    now = time.time()
    with _lock:
        _gc(now)
        token = secrets.token_urlsafe(24)
        _pending[token] = (user_id, now + PENDING_TTL)
    return token


def peek_pending(token: str) -> int:
    """Вернуть user_id по токену или 401 — НЕ расходует токен (опечатка в коде
    не должна заставлять вводить пароль заново; лимит попыток — ratelimit.guard_totp)."""
    now = time.time()
    with _lock:
        _gc(now)
        entry = _pending.get(token)
    if not entry or entry[1] < now:
        raise HTTPException(status_code=401, detail="Сессия входа истекла, войдите заново")
    return entry[0]


def consume_pending(token: str) -> None:
    """Вызывать после УСПЕШНОЙ проверки кода — токен одноразовый на успех."""
    with _lock:
        _pending.pop(token, None)
