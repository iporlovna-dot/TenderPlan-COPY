"""Конфиг бэкенда — из окружения (secure by design: секреты не в коде)."""
from __future__ import annotations

import os
import secrets

from pydantic_settings import BaseSettings, SettingsConfigDict


def _dev_secret() -> str:
    """Локальный dev-секрет: генерируем ОДИН раз и сохраняем в data/.dev_secret, чтобы токены
    переживали перезапуск сервера (иначе на каждом рестарте всех разлогинивает). В проде
    секрет задаётся через SPECMATCH_SECRET_KEY и этот путь не используется."""
    path = os.path.join(os.path.dirname(__file__), "..", "data", ".dev_secret")
    try:
        if os.path.exists(path):
            return open(path).read().strip()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        val = secrets.token_urlsafe(48)
        open(path, "w").write(val)
        return val
    except Exception:
        return secrets.token_urlsafe(48)  # не смогли записать — эфемерный


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPECMATCH_", env_file=".env", extra="ignore")

    # JWT: в проде задаётся SPECMATCH_SECRET_KEY. Локально — стабильный секрет из data/.dev_secret.
    secret_key: str = _dev_secret()
    jwt_algorithm: str = "HS256"
    access_token_ttl_min: int = 60
    refresh_token_ttl_days: int = 30      # refresh живёт долго, но отзываем (ротация + reuse-detection)

    database_url: str = "sqlite:///./data/app.db"

    # Rate-limit логина (защита от перебора): лимит неудач на (IP+email), затем временная
    # блокировка (НЕ вечная). Многопроцессность: задай SPECMATCH_REDIS_URL — счётчик станет общим
    # (иначе in-memory на процесс). Redis недоступен → мягкая деградация в in-memory.
    login_max_fails: int = 5
    login_lockout_sec: int = 900          # ~15 минут
    redis_url: str = ""                   # напр. redis://localhost:6379/0 — общий стор rate-limit

    # Прод-харденинг: за HTTPS-прокси включить → добавляется HSTS (принудительный TLS в браузере).
    https_only: bool = False

    # Каталог, куда CLI-конвейер (run_auto --out) пишет вердикты для ingest в ленту.
    results_dir: str = "data/results"


settings = Settings()
