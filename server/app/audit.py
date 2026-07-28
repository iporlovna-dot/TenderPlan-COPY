"""Аудит безопасности: вход/выход, 2FA, админ-доступ.

Пишем в stdout → его забирает systemd journal (`journalctl -u tender-api`).
Отдельный логгер `lekalo.audit` с префиксом `AUDIT`, чтобы легко грепать и не
смешивать с логами uvicorn. Никаких паролей/токенов/кодов в аудит НЕ пишем —
только факт события, IP, id/email пользователя."""

from __future__ import annotations

import logging
import sys

_logger = logging.getLogger("lekalo.audit")
if not _logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(asctime)s AUDIT %(message)s"))
    _logger.addHandler(_h)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False  # не дублировать в корневой логгер uvicorn


def log(event: str, **fields) -> None:
    """Строка вида `login_fail ip=1.2.3.4 email=a@b.ru`. Значения приводим к str
    и вычищаем перевод строки, чтобы никто не подделал лишнюю строку в логе."""
    parts = [event]
    for k, v in fields.items():
        s = str(v).replace("\n", " ").replace("\r", " ")
        parts.append(f"{k}={s}")
    _logger.info(" ".join(parts))
