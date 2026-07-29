"""FastAPI-приложение SpecMatch (MVP). Запуск:

    .venv/bin/uvicorn api.main:app --reload
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import FileResponse

from api.db import init_db
from api.routers import auth, leads, products

app = FastAPI(title="SpecMatch API", version="0.1.0",
              description="Подбор госзакупок под товар по характеристикам ТЗ (MVP)")

_WEB = os.path.join(os.path.dirname(__file__), "..", "web", "index.html")


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
