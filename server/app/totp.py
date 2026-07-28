"""Двухфакторная аутентификация (TOTP — Google Authenticator/Authy и т.п.).

Секрет хранится в users.totp_secret, включается только после первого верного
кода (totp_enabled). Второй шаг входа — краткоживущий (5 мин) токен в памяти,
не в БД: он не переживает рестарт процесса, но это ровно то, что нужно —
если сервер перезапустился между вводом пароля и вводом кода, просто входим
заново с начала."""

from __future__ import annotations

import io
import os
import secrets
import threading
import time

import pyotp
import qrcode
import qrcode.image.svg
from fastapi import HTTPException

try:  # cryptography приходит транзитивно с pdfplumber; на всякий — мягкий импорт
    from cryptography.fernet import Fernet, InvalidToken
except Exception:  # pragma: no cover
    Fernet = None  # type: ignore
    InvalidToken = Exception  # type: ignore

ISSUER = "Лекало"
PENDING_TTL = 300       # 5 минут на ввод кода после пароля
MAX_PENDING_FAILS = 5   # столько неверных кодов на ОДИН токен входа — потом токен сгорает


# ---------- шифрование TOTP-секрета в БД (at-rest) ----------
# Если БД утечёт (без сервера), plaintext-секрет позволил бы обойти 2FA.
# Ключ — в env LK_TOTP_KEY (Fernet, `Fernet.generate_key()`); нет ключа →
# храним как раньше (обратная совместимость, чтобы 2FA не отвалилась на проде,
# где ключ ещё не задан). Формат хранения: "enc:<...>" для зашифрованных.
_ENC_PREFIX = "enc:"
_key = os.getenv("LK_TOTP_KEY")
_fernet = Fernet(_key.encode()) if (_key and Fernet) else None


def protect_secret(secret: str) -> str:
    """Подготовить секрет к записи в БД (зашифровать, если задан ключ)."""
    if not _fernet or not secret:
        return secret
    return _ENC_PREFIX + _fernet.encrypt(secret.encode()).decode()


def reveal_secret(stored: str | None) -> str:
    """Достать рабочий секрет из хранимого значения (расшифровать при необходимости)."""
    if not stored:
        return ""
    if not stored.startswith(_ENC_PREFIX):
        return stored  # старый plaintext-секрет — как есть
    if not _fernet:
        return ""       # зашифровано, но ключа нет → верификация честно провалится
    try:
        return _fernet.decrypt(stored[len(_ENC_PREFIX):].encode()).decode()
    except InvalidToken:
        return ""


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
# Значение: {"uid": user_id, "exp": deadline, "fails": счётчик неверных кодов}.

_pending: dict[str, dict] = {}
_lock = threading.Lock()


def _gc(now: float) -> None:
    for tok, e in list(_pending.items()):
        if e["exp"] < now:
            _pending.pop(tok, None)


def create_pending(user_id: int) -> str:
    now = time.time()
    with _lock:
        _gc(now)
        token = secrets.token_urlsafe(24)
        _pending[token] = {"uid": user_id, "exp": now + PENDING_TTL, "fails": 0}
    return token


def peek_pending(token: str) -> int:
    """Вернуть user_id по токену или 401 — НЕ расходует токен (опечатка в коде
    не должна заставлять вводить пароль заново; лимит попыток — guard_totp по IP
    плюс fail_pending: N неверных кодов на этот токен и он сгорает)."""
    now = time.time()
    with _lock:
        _gc(now)
        entry = _pending.get(token)
    if not entry or entry["exp"] < now:
        raise HTTPException(status_code=401, detail="Сессия входа истекла, войдите заново")
    return entry["uid"]


def fail_pending(token: str) -> None:
    """Неверный код: жжём попытки этого токена. После MAX_PENDING_FAILS токен
    сгорает — даже с пула IP не перебрать 6 цифр по одному входу (нужен новый
    вход с паролем за каждые 5 догадок)."""
    with _lock:
        e = _pending.get(token)
        if not e:
            return
        e["fails"] += 1
        if e["fails"] >= MAX_PENDING_FAILS:
            _pending.pop(token, None)


def consume_pending(token: str) -> None:
    """Вызывать после УСПЕШНОЙ проверки кода — токен одноразовый на успех."""
    with _lock:
        _pending.pop(token, None)
