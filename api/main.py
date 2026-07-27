"""FastAPI-приложение SpecMatch (MVP). Запуск:

    .venv/bin/uvicorn api.main:app --reload
"""
from __future__ import annotations

from fastapi import FastAPI

from api.db import init_db
from api.routers import auth, leads, products

app = FastAPI(title="SpecMatch API", version="0.1.0",
              description="Подбор госзакупок под товар по характеристикам ТЗ (MVP)")


@app.on_event("startup")
def _startup():
    init_db()


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(products.router)
app.include_router(leads.router)
