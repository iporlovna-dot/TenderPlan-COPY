"""FastAPI-бэкенд площадки поиска торгов.

Эндпоинты:
  GET  /api/health
  GET  /api/purchases      — лента с фильтрами (поиск по ключевым словам — на нашей стороне)
  GET  /api/purchases/{id} — полная карточка (позиции, сроки, документы)
  GET  /api/documents/{fid}— прокси-скачивание вложения закупки
  POST /api/match          — загрузить своё ТЗ + purchase_id → сверка

Источник пока один (Портал поставщиков); добавление площадок — новый Source за тем
же интерфейсом, ядро не меняется."""

from __future__ import annotations

import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app import matching
from app.schema import Purchase, PurchasePage
from app.sources.portal import PortalPostavshikov

POOL_TAKE = int(os.getenv("LK_POOL_TAKE", "200"))
POOL_TTL = int(os.getenv("LK_POOL_TTL", "300"))  # сек
CORS_ORIGINS = os.getenv("LK_CORS_ORIGINS", "*").split(",")

_state: dict = {"client": None, "source": None, "pool": [], "pool_ts": 0.0}


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = httpx.AsyncClient(timeout=40.0, follow_redirects=True)
    _state["client"] = client
    _state["source"] = PortalPostavshikov(client)
    yield
    await client.aclose()


app = FastAPI(title="Лекало — поиск торгов", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------- пул закупок (кэш) ----------

async def _get_pool() -> list[Purchase]:
    now = time.time()
    if _state["pool"] and now - _state["pool_ts"] < POOL_TTL:
        return _state["pool"]
    pool = await _state["source"].search(take=POOL_TAKE)
    _state["pool"] = pool
    _state["pool_ts"] = now
    return pool


# ---------- поиск по ключевым словам (наша сторона) ----------

def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[\s,]+", (s or "").lower()) if t]


def _stem(w: str) -> str:
    return w if len(w) <= 4 else w[:-2]


def _passes_search(p: Purchase, plus: list[str], minus: list[str]) -> bool:
    hay = f"{p.title} {p.customer} {p.number} {p.okpd}".lower()
    if any(_stem(m) in hay for m in minus):
        return False
    if plus and not any(_stem(w) in hay for w in plus):
        return False
    return True


# ---------- эндпоинты ----------

@app.get("/api/health")
async def health():
    return {"ok": True, "source": _state["source"].name if _state["source"] else None}


@app.get("/api/purchases", response_model=PurchasePage)
async def purchases(
    query: str = Query("", description="ключевые слова через пробел"),
    minus: str = Query("", description="минус-слова"),
    law: str = Query("all"),
    region: str = Query("all"),
    stage: str = Query("all"),
    source: str = Query("all"),
    price_min: float = Query(0),
    price_max: float | None = Query(None),
    take: int = Query(30, ge=1, le=100),
    skip: int = Query(0, ge=0),
):
    pool = await _get_pool()
    plus, mns = _tokens(query), _tokens(minus)

    def ok(p: Purchase) -> bool:
        if law != "all" and p.law != law:
            return False
        if region != "all" and p.region != region:
            return False
        if stage != "all" and p.stage != stage:
            return False
        if source != "all" and p.source != source:
            return False
        if p.price < price_min:
            return False
        if price_max is not None and p.price > price_max:
            return False
        return _passes_search(p, plus, mns)

    filtered = [p for p in pool if ok(p)]
    page = filtered[skip:skip + take]
    return PurchasePage(
        total=len(filtered),
        generatedAt=datetime.now(timezone.utc).isoformat(),
        source=_state["source"].name,
        purchases=page,
    )


@app.get("/api/purchases/{purchase_id}", response_model=Purchase)
async def purchase_card(purchase_id: str):
    try:
        return await _state["source"].card(purchase_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=404, detail=f"Закупка не найдена: {e}") from e


@app.get("/api/documents/{file_id}")
async def document(file_id: str):
    try:
        d = await _state["source"].download(file_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=404, detail="Файл не найден") from e
    return Response(
        content=d.content,
        media_type=d.content_type,
        headers={"Content-Disposition": f'attachment; filename="{d.filename}"'},
    )


@app.post("/api/match")
async def match(purchase_id: str = Form(...), file: UploadFile = File(...)):
    """Сверка загруженного ТЗ товара с ТЗ закупки."""
    product_bytes = await file.read()
    product_text = matching.extract_text(file.filename or "spec.txt", product_bytes)

    src = _state["source"]
    card = await src.card(purchase_id)

    # ТЗ закупки: берём первый «текстовый» документ (описание/ТЗ), иначе — название
    purchase_text = card.title
    doc = _pick_tz(card)
    if doc is not None:
        try:
            dl = await src.download(doc.id)
            purchase_text = matching.extract_text(dl.filename, dl.content) or purchase_text
        except Exception:
            pass

    result = matching.compare(purchase_text, product_text, product_id="uploaded")
    return {"purchase_id": purchase_id, "match": result}


def _pick_tz(card: Purchase):
    keys = ("тз", "техническ", "описание", "объект")
    for d in card.documents:
        low = d.name.lower()
        if any(k in low for k in keys) and low.endswith((".docx", ".pdf", ".txt", ".xlsx")):
            return d
    for d in card.documents:
        if d.name.lower().endswith((".docx", ".pdf", ".txt")):
            return d
    return None
