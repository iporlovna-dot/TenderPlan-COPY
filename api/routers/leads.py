"""Лента подходящих закупок под товар — ранжированная, изолированная по компании.

Плюс: деталь лида с разбором по требованиям и ДОЗАПОЛНЕНИЕ ПРОБЕЛОВ (§Этап 1, фича founder) —
клиент вносит недостающее значение под пробел → дописывается в карточку → % пересчитывается
реальным матчером (ядро в src/gapfill.py; LLM не участвует — извлечение уже заморожено)."""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import get_current_user
from api.models import Lead, Product, User
from api.schemas import CheckOut, GapFillIn, LeadDetailOut, LeadOut
from gapfill import recompute

router = APIRouter(prefix="/products", tags=["leads"])


def _days_left(submission_close):
    if not submission_close:
        return None
    return round((submission_close / 1000.0 - time.time()) / 86400.0, 1)


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


def _detail(lead: Lead, res, attrs: list) -> LeadDetailOut:
    return LeadDetailOut(
        purchase_id=lead.purchase_id, subject=lead.subject, score=res.score,
        verdict=res.verdict.value, explanation=res.explanation,
        checks=[CheckOut(req=c.req.key, status=c.status.value, note=c.note, action=c.action)
                for c in res.checks],
        gaps=[c.req.key for c in res.checks if c.status.value == "gap"],
        attributes=attrs,
    )


def _recompute_lead(p: Product, lead: Lead, fills=None):
    """Пересчёт вердикта лида по текущей карточке + замороженным требованиям (±дозаполнение)."""
    product = {"id": p.product_key, "name": p.name,
               "attributes": json.loads(p.attributes_json or "[]")}
    reqs = json.loads(lead.requirements_json or "[]")
    syn = json.loads(lead.synonyms_json or "{}")
    return recompute(product, reqs, lead.purchase_id, fills=fills, synonyms=syn)


@router.get("/{product_id}/leads", response_model=list[LeadOut])
def product_leads(product_id: int, min_score: int = Query(default=60, ge=0, le=100),
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # товар должен принадлежать компании пользователя
    p = db.query(Product).filter(Product.id == product_id,
                                 Product.company_id == user.company_id).first()
    if p is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")

    rows = (db.query(Lead)
            .filter(Lead.company_id == user.company_id, Lead.product_id == product_id,
                    Lead.verdict != "disqualified", Lead.score >= min_score)
            .order_by(Lead.score.desc())
            .all())
    return [LeadOut(
        purchase_id=r.purchase_id, subject=r.subject, customer=r.customer, price=r.price,
        region=r.region, submission_close=r.submission_close,
        days_left=_days_left(r.submission_close), url=r.url, score=r.score,
        verdict=r.verdict, explanation=r.explanation,
    ) for r in rows]


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
