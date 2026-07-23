"""Обновить статический снапшот site/data/purchases.json из источника.

Используется на сервере по расписанию (cron) — на случай, если фронт работает
как статика (GitHub Pages) без живого API. Запуск из папки server/:
    cd server && .venv/bin/python scripts/refresh_snapshot.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
_SERVER = os.path.dirname(_SCRIPTS)
_ROOT = os.path.dirname(_SERVER)
sys.path.insert(0, _SERVER)  # чтобы импортировался пакет app/

import httpx  # noqa: E402

from app.sources.portal import PortalPostavshikov  # noqa: E402

OUT = os.path.join(_ROOT, "site", "data", "purchases.json")
TAKE = int(os.getenv("LK_SNAPSHOT_TAKE", "60"))


async def main() -> None:
    async with httpx.AsyncClient(timeout=40.0, follow_redirects=True) as client:
        src = PortalPostavshikov(client)
        purchases = await src.search(take=TAKE)

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": src.name + " — активные котировочные сессии",
        "total": len(purchases),
        "purchases": [p.model_dump() for p in purchases],
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"wrote {len(purchases)} purchases -> {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
