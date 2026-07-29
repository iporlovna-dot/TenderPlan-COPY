"""Лента подходящих закупок под товар — ранжированная, изолированная по компании.

Плюс: деталь лида с разбором по требованиям и ДОЗАПОЛНЕНИЕ ПРОБЕЛОВ (§Этап 1, фича founder) —
клиент вносит недостающее значение под пробел → дописывается в карточку → % пересчитывается
реальным матчером (ядро в src/gapfill.py; LLM не участвует — извлечение уже заморожено)."""
from __future__ import annotations

import json
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import get_current_user
from api.models import Lead, Product, User
from api.schemas import CheckOut, ContractCard, GapFillIn, LeadDetailOut, LeadOut, LeadPage
from gapfill import recompute
from versioning import change_message

router = APIRouter(prefix="/products", tags=["leads"])


def _days_left(submission_close):
    if not submission_close:
        return None
    return round((submission_close / 1000.0 - time.time()) / 86400.0, 1)


def _card(lead: Lead):
    """Карточка контракта из card_json + расчёт «дней до окончания подачи». None, если пусто."""
    d = json.loads(lead.card_json or "{}")
    if not d:
        return None
    d = {k: v for k, v in d.items() if k in ContractCard.model_fields}
    d["days_to_submission"] = _days_left(d.get("submission_close"))
    try:
        return ContractCard(**d)
    except Exception:
        return None


def _load_product_lead(product_id: int, purchase_id: str, user: User, db: Session):
    """Товар и лид компании пользователя (изоляция) или 404."""
    p = db.query(Product).filter(Product.id == product_id,
                                 Product.company_id == user.company_id).first()
    if p is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")
    lead = (db.query(Lead)
            .filter(Lead.company_id == user.company_id, Lead.product_id == product_id,
                    Lead.purchase_id == purchase_id).first())
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Лид не найден")
    return p, lead


_OP_WORD = {"gte": "не менее", "lte": "не более", "range": "в диапазоне", "one_of": "одно из",
            "set": "набор", "eq": "", "present": "наличие"}


def _req_text(req) -> str:
    """Человекочитаемая формулировка требования ТЗ: дословная (raw) или собранная из оператора+значения."""
    if getattr(req, "raw", ""):
        return req.raw
    op = req.operator.value if hasattr(req.operator, "value") else str(req.operator)
    if op == "present":
        return "наличие"
    v = req.value
    if isinstance(v, list):
        v = ", ".join(str(x) for x in v)
    return (" ".join(x for x in [_OP_WORD.get(op, ""), str(v), req.unit or ""] if x)).strip()


def _detail(lead: Lead, res, attrs: list) -> LeadDetailOut:
    amap = {a["key"]: a.get("value") for a in (attrs or [])}

    def _mine(key):
        v = amap.get(key)
        if v is None:
            return None
        return ", ".join(str(x) for x in v) if isinstance(v, list) else str(v)

    return LeadDetailOut(
        purchase_id=lead.purchase_id, subject=lead.subject, score=res.score,
        verdict=res.verdict.value, explanation=res.explanation,
        checks=[CheckOut(req=c.req.key, status=c.status.value, note=c.note, action=c.action,
                         req_text=_req_text(c.req), product_value=_mine(c.req.key))
                for c in res.checks],
        gaps=[c.req.key for c in res.checks if c.status.value == "gap"],
        attributes=attrs,
        card=_card(lead),
        change_status=lead.change_status,
        change_note=change_message(lead.change_status),
    )


def _recompute_lead(p: Product, lead: Lead, fills=None):
    """Пересчёт вердикта лида по текущей карточке + замороженным требованиям (±дозаполнение)."""
    product = {"id": p.product_key, "name": p.name,
               "attributes": json.loads(p.attributes_json or "[]")}
    reqs = json.loads(lead.requirements_json or "[]")
    syn = json.loads(lead.synonyms_json or "{}")
    return recompute(product, reqs, lead.purchase_id, fills=fills, synonyms=syn)


def _to_leadout(r: Lead) -> LeadOut:
    return LeadOut(
        product_id=r.product_id,
        purchase_id=r.purchase_id, subject=r.subject, customer=r.customer, price=r.price,
        region=r.region, submission_close=r.submission_close,
        days_left=_days_left(r.submission_close), url=r.url, score=r.score,
        verdict=r.verdict, explanation=r.explanation, card=_card(r),
        change_status=r.change_status, change_note=change_message(r.change_status),
    )


def _leads_page(db, company_id, product_id, min_score, include_disqualified, changed_only,
                region, deadline_max_days, sort, limit, offset):
    """Общий построитель ленты: фильтры → счётчик → сортировка → окно. Изоляция по company_id."""
    q = db.query(Lead).filter(Lead.company_id == company_id, Lead.score >= min_score)
    if product_id is not None:
        q = q.filter(Lead.product_id == product_id)
    if not include_disqualified:
        q = q.filter(Lead.verdict != "disqualified")
    if changed_only:                                 # «требуют внимания»: ТЗ изменилось по сути
        q = q.filter(Lead.change_status == "material")
    if region:
        q = q.filter(Lead.region == region)
    if deadline_max_days is not None:                # только закрывающиеся в окне [сейчас, +N дней]
        now_ms = int(time.time() * 1000)
        q = q.filter(Lead.submission_close.isnot(None), Lead.submission_close >= now_ms,
                     Lead.submission_close <= now_ms + int(deadline_max_days * 86_400_000))
    total = q.count()
    if sort == "deadline":                           # срочные выше (nulls в конец)
        q = q.order_by(Lead.submission_close.is_(None), Lead.submission_close.asc())
    elif sort == "updated":
        q = q.order_by(Lead.updated_at.desc())
    else:                                            # score (дефолт) — по убыванию %
        q = q.order_by(Lead.score.desc())
    rows = q.offset(offset).limit(limit).all()
    return total, rows


# --- общие query-параметры ленты (пагинация/фильтры/сортировка) ---
def _lead_params(min_score: int = Query(0, ge=0, le=100),
                 include_disqualified: bool = Query(False),
                 changed_only: bool = Query(False, description="только material-изменения (внимание)"),
                 region: Optional[str] = Query(None),
                 deadline_max_days: Optional[float] = Query(None, ge=0),
                 sort: str = Query("score", pattern="^(score|deadline|updated)$"),
                 limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    return dict(min_score=min_score, include_disqualified=include_disqualified,
                changed_only=changed_only, region=region, deadline_max_days=deadline_max_days,
                sort=sort, limit=limit, offset=offset)


@router.get("/{product_id}/leads", response_model=LeadPage)
def product_leads(product_id: int, params: dict = Depends(_lead_params),
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Лента по одному товару — пагинация/фильтры/сортировка. Товар должен быть компании."""
    p = db.query(Product).filter(Product.id == product_id,
                                 Product.company_id == user.company_id).first()
    if p is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")
    total, rows = _leads_page(db, user.company_id, product_id, **params)
    return LeadPage(total=total, limit=params["limit"], offset=params["offset"],
                    items=[_to_leadout(r) for r in rows])


@router.get("/{product_id}/leads/{purchase_id}", response_model=LeadDetailOut)
def lead_detail(product_id: int, purchase_id: str,
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Деталь лида: разбор по требованиям (pass/violation/gap) + список пробелов для дозаполнения.
    Считается живьём по ТЕКУЩЕЙ карточке (учитывает ранее внесённые дозаполнения)."""
    p, lead = _load_product_lead(product_id, purchase_id, user, db)
    res, attrs = _recompute_lead(p, lead)
    lead.score, lead.verdict, lead.explanation = res.score, res.verdict.value, res.explanation
    db.commit()
    return _detail(lead, res, attrs)


@router.post("/{product_id}/leads/{purchase_id}/fill", response_model=LeadDetailOut)
def lead_fill(product_id: int, purchase_id: str, body: GapFillIn,
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Дозаполнить пробелы: значения клиента дописываются в КАРТОЧКУ (status confirmable) →
    % пересчитывается реальным матчером. Введённое значение, которое не проходит, честно
    остаётся нарушением/пробелом — не «зачитывается» вслепую."""
    p, lead = _load_product_lead(product_id, purchase_id, user, db)
    res, attrs = _recompute_lead(p, lead, fills=body.fills)
    p.attributes_json = json.dumps(attrs, ensure_ascii=False)   # карточка «учится» дозаполнением
    lead.score, lead.verdict, lead.explanation = res.score, res.verdict.value, res.explanation
    db.commit()
    return _detail(lead, res, attrs)


# --- Сводная лента ПО ВСЕЙ КОМПАНИИ (агрегирует все товары, §9) ---
feed_router = APIRouter(prefix="/leads", tags=["feed"])


@feed_router.get("", response_model=LeadPage)
def company_feed(params: dict = Depends(_lead_params),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Единая лента компании по ВСЕМ товарам (сценарий «поставщик с N позициями»). Те же
    пагинация/фильтры/сортировка. `changed_only=true` → «требуют внимания» (ТЗ изменилось)."""
    total, rows = _leads_page(db, user.company_id, None, **params)
    return LeadPage(total=total, limit=params["limit"], offset=params["offset"],
                    items=[_to_leadout(r) for r in rows])
