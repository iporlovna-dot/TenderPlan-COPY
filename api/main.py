"""FastAPI-приложение SpecMatch (MVP). Запуск:

    .venv/bin/uvicorn api.main:app --reload
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse

from api.config import settings
from api.db import init_db
from api.routers import auth, leads, products

app = FastAPI(title="SpecMatch API", version="0.1.0",
              description="Подбор госзакупок под товар по характеристикам ТЗ (MVP)")

_WEB = os.path.join(os.path.dirname(__file__), "..", "web", "index.html")


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Базовый харденинг ответов (secure by design, §5). HSTS — только когда за HTTPS
    (`SPECMATCH_HTTPS_ONLY=1`), иначе браузер бы залочил и локальный http."""
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")      # без MIME-sniffing
    resp.headers.setdefault("X-Frame-Options", "DENY")                # без встраивания в iframe (clickjacking)
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if settings.https_only:
        resp.headers.setdefault("Strict-Transport-Security",
                                "max-age=31536000; includeSubDomains")  # принудительный TLS
    return resp


@app.on_event("startup")
def _startup():
    init_db()


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def cabinet():
    """Веб-кабинет поставщика (SPA на одной странице; API — на тех же ручках)."""
    return FileResponse(_WEB)


app.include_router(auth.router)
app.include_router(products.router)
app.include_router(leads.router)
app.include_router(leads.feed_router)   # сводная лента компании (GET /leads)
