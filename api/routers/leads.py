"""Лента подходящих закупок под товар — ранжированная, изолированная по компании."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import get_current_user
from api.models import Lead, Product, User
from api.schemas import LeadOut

router = APIRouter(prefix="/products", tags=["leads"])


def _days_left(submission_close):
    if not submission_close:
        return None
    return round((submission_close / 1000.0 - time.time()) / 86400.0, 1)


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
