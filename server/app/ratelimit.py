"""In-memory rate-limit для чувствительных эндпоинтов (вход/регистрация).

Зачем: без лимита `/auth/login` можно долбить бесконечно — это и подбор пароля,
и CPU-DoS (bcrypt намеренно дорогой; поток неудачных логинов выжигает процессор).
Поэтому лимит проверяется ДО проверки пароля.

Схема минимальная под один VPS: один процесс uvicorn → общий словарь в памяти.
При рестарте счётчики обнуляются — для троттлинга это нормально. Если когда-нибудь
поднимем несколько воркеров/инстансов — заменить на Redis (тот же интерфейс).
"""

from __future__ import annotations

import os
import threading
import time

from fastapi import HTTPException, Request


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# Порог: сколько срабатываний за окно (сек). Настраивается через env.
LOGIN_IP_MAX = _int("LK_RL_LOGIN_IP_MAX", 10)        # неудачных логинов с одного IP
LOGIN_IP_WINDOW = _int("LK_RL_LOGIN_IP_WINDOW", 300)  # за 5 минут
LOGIN_EMAIL_MAX = _int("LK_RL_LOGIN_EMAIL_MAX", 5)    # неудачных на один email
LOGIN_EMAIL_WINDOW = _int("LK_RL_LOGIN_EMAIL_WINDOW", 900)  # за 15 минут
REGISTER_IP_MAX = _int("LK_RL_REGISTER_IP_MAX", 8)    # регистраций с одного IP
REGISTER_IP_WINDOW = _int("LK_RL_REGISTER_IP_WINDOW", 3600)  # за час
TOTP_MAX = _int("LK_RL_TOTP_MAX", 8)                  # неудачных кодов 2FA
TOTP_WINDOW = _int("LK_RL_TOTP_WINDOW", 300)          # за 5 минут (код 6 цифр — жёстче, чем пароль)

# Предохранитель памяти: email в запросе задаёт атакующий, поэтому число ключей
# ограничиваем — иначе поток разных email раздул бы словарь (DoS через сам лимитер).
MAX_KEYS = _int("LK_RL_MAX_KEYS", 20000)


class _Window:
    """Скользящее окно: список меток времени на ключ, потокобезопасно."""

    def __init__(self, max_hits: int, window: int):
        self.max = max_hits
        self.window = window
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> list[float]:
        q = self._hits.get(key)
        if q is None:
            return []
        cutoff = now - self.window
        while q and q[0] < cutoff:
            q.pop(0)
        if not q:
            self._hits.pop(key, None)
        return q

    def _gc(self, now: float) -> None:
        # Аварийная чистка при переполнении: выкинуть все просроченные окна.
        if len(self._hits) <= MAX_KEYS:
            return
        cutoff = now - self.window
        for k in list(self._hits.keys()):
            q = self._hits[k]
            while q and q[0] < cutoff:
                q.pop(0)
            if not q:
                self._hits.pop(k, None)
        # если и после чистки тесно — сбрасываем целиком (грубо, но память ограничена)
        if len(self._hits) > MAX_KEYS:
            self._hits.clear()

    def over(self, key: str) -> bool:
        """Лимит уже достигнут? (только читает, счётчик не трогает)."""
        now = time.time()
        with self._lock:
            return len(self._prune(key, now)) >= self.max

    def hit(self, key: str) -> int:
        """Зафиксировать срабатывание, вернуть их число в окне."""
        now = time.time()
        with self._lock:
            self._gc(now)
            q = self._hits.setdefault(key, [])
            cutoff = now - self.window
            while q and q[0] < cutoff:
                q.pop(0)
            q.append(now)
            return len(q)

    def retry_after(self, key: str) -> int:
        """Через сколько секунд освободится самый старый слот."""
        now = time.time()
        with self._lock:
            q = self._hits.get(key)
            if not q:
                return 0
            return max(1, int(self.window - (now - q[0])) + 1)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


_login_ip = _Window(LOGIN_IP_MAX, LOGIN_IP_WINDOW)
_login_email = _Window(LOGIN_EMAIL_MAX, LOGIN_EMAIL_WINDOW)
_register_ip = _Window(REGISTER_IP_MAX, REGISTER_IP_WINDOW)
_totp_ip = _Window(TOTP_MAX, TOTP_WINDOW)


def client_ip(request: Request) -> str:
    """Реальный IP из-за nginx. X-Real-IP nginx ставит сам (= TCP-пир, не подделать);
    X-Forwarded-For доверять нельзя — клиент может дописать. Фолбэк — прямой пир."""
    xr = request.headers.get("x-real-ip")
    if xr:
        return xr.strip()
    return request.client.host if request.client else "unknown"


def _too_many(retry: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail="Слишком много попыток. Попробуйте позже.",
        headers={"Retry-After": str(max(1, retry))},
    )


def guard_login(ip: str, email: str) -> None:
    """Вызывать ДО проверки пароля. 429, если IP или email уже за лимитом."""
    ik, ek = "ip:" + ip, "em:" + email
    if _login_ip.over(ik):
        raise _too_many(_login_ip.retry_after(ik))
    if _login_email.over(ek):
        raise _too_many(_login_email.retry_after(ek))


def record_login_failure(ip: str, email: str) -> None:
    """Считаем только НЕУДАЧНЫЕ входы — верный пароль штрафа не получает."""
    _login_ip.hit("ip:" + ip)
    _login_email.hit("em:" + email)


def reset_login(ip: str, email: str) -> None:
    """Успешный вход обнуляет счётчики (чтобы не копить у живого юзера)."""
    _login_ip.reset("ip:" + ip)
    _login_email.reset("em:" + email)


def guard_register(ip: str) -> None:
    """Троттлинг регистраций по IP (спам-компании + тот же дорогой bcrypt)."""
    key = "ip:" + ip
    if _register_ip.hit(key) > _register_ip.max:
        raise _too_many(_register_ip.retry_after(key))


def guard_totp(ip: str) -> None:
    """Вызывать ДО проверки кода 2FA — 6 цифр перебираются быстро без лимита."""
    key = "ip:" + ip
    if _totp_ip.over(key):
        raise _too_many(_totp_ip.retry_after(key))


def record_totp_failure(ip: str) -> None:
    _totp_ip.hit("ip:" + ip)


def reset_totp(ip: str) -> None:
    _totp_ip.reset("ip:" + ip)
