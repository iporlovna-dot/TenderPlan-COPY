"""FastAPI-бэкенд площадки поиска торгов.

Эндпоинты:
  GET  /api/health
  GET  /api/purchases      — лента с фильтрами (поиск по ключевым словам — на нашей стороне)
  GET  /api/purchases/{id} — полная карточка (позиции, сроки, документы)
  GET  /api/documents/{fid}— прокси-скачивание вложения закупки
  POST /api/match          — загрузить своё ТЗ + purchase_id → сверка текст↔текст
  POST /api/match/spec     — карточки товара ↔ позиции закупки, движком matcher/

Источник пока один (Портал поставщиков); добавление площадок — новый Source за тем
же интерфейсом, ядро не меняется.

⚠️ Эндпоинты, ходящие в ИСТОЧНИК (/api/purchases, /api/documents, /api/match), на
проде нерабочие: VPS заблокирован zakupki.gov.ru и mos.ru. /api/match/spec к ним
не относится — он читает уже собранные данные с диска (site/data/tz.json)."""

from __future__ import annotations

import asyncio
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import Cookie, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app import matching, spec_match
from app.accounts import _require_active_user, router as accounts_router
from app.invoices import router as invoices_router
from app.support import poll_updates, router as support_router
from app.db import init_db
from app.schema import Purchase, PurchasePage
from app.sources.portal import PortalPostavshikov

POOL_TAKE = int(os.getenv("LK_POOL_TAKE", "200"))
POOL_TTL = int(os.getenv("LK_POOL_TTL", "300"))  # сек
# Потолок на размер каталога в одном запросе сверки: сверка идёт в процессе
# API, и присланный кем-то список на десятки тысяч позиций съел бы его целиком.
MATCH_MAX_PRODUCTS = int(os.getenv("LK_MATCH_MAX_PRODUCTS", "500"))
# Фронт живёт на том же домене (nginx проксирует /api) → CORS-звёздочка не нужна.
# Вход/кабинет работают на https://…nip.io — его и разрешаем. Несколько origin'ов — через запятую.
CORS_ORIGINS = [o.strip() for o in os.getenv("LK_CORS_ORIGINS", "https://147.45.141.237.nip.io").split(",") if o.strip()]

_state: dict = {"client": None, "source": None, "pool": [], "pool_ts": 0.0}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    client = httpx.AsyncClient(timeout=40.0, follow_redirects=True)
    _state["client"] = client
    _state["source"] = PortalPostavshikov(client)
    # Telegram-апдейты приходят long-polling'ом, не вебхуком — см. докстринг
    # app/support.py (2026-08-21: вебхук не получал трафика от Telegram).
    poll_task = asyncio.create_task(poll_updates())
    yield
    poll_task.cancel()
    try:
        await poll_task
    except asyncio.CancelledError:
        pass
    await client.aclose()


app = FastAPI(title="Лекало — поиск торгов", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,  # cookie-сессия работает same-origin; НЕ включать вместе с "*"
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)
app.include_router(accounts_router)
app.include_router(invoices_router)
app.include_router(support_router)


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


@app.post("/api/match/spec")
async def match_spec(payload: dict, lekalo_session: str | None = Cookie(default=None)):
    """Сверка карточек товара с ПОЗИЦИЯМИ закупки — настоящим движком matcher/.

    Отличие от /api/match ниже принципиальное: там текст против текста («это
    вообще про мой товар?»), здесь структура против структуры («а по параметрам
    проходим?»). Требования берутся из таблицы КТРУ, собранной сборщиком
    (site/data/tz.json), товары приходят из кабинета.

    Источники этому эндпоинту не нужны — он читает уже собранные данные с диска,
    поэтому работает на VPS, заблокированном zakupki.gov.ru и mos.ru.

    Тело: {"purchase_id": "eis_123", "products": [{id, name, ktru, attributes}]}

    Требует активный тариф/демо (_require_active_user) — до этой правки
    эндпоинт был вообще без авторизации, хотя вызывается только из кабинета
    (site/js/appview.js ensureProdMatch, credentials: "same-origin").
    """
    conn, _ = _require_active_user(lekalo_session)
    conn.close()
    purchase_id = (payload or {}).get("purchase_id")
    if not purchase_id:
        raise HTTPException(status_code=400, detail="нужен purchase_id")
    products = (payload or {}).get("products") or []
    if len(products) > MATCH_MAX_PRODUCTS:
        raise HTTPException(status_code=413,
                            detail=f"слишком много товаров (максимум {MATCH_MAX_PRODUCTS})")
    return spec_match.match_lot(str(purchase_id), products)


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
