"""CRUD товаров, изолированный по компании текущего пользователя."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import get_current_user
from api.models import Product, User
from api.schemas import ProductIn, ProductOut

router = APIRouter(prefix="/products", tags=["products"])


def _to_out(p: Product) -> ProductOut:
    return ProductOut(id=p.id, product_key=p.product_key, name=p.name,
                      ktru=json.loads(p.ktru_json or "[]"),
                      attributes=json.loads(p.attributes_json or "[]"))


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(data: ProductIn, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    p = Product(
        company_id=user.company_id, product_key=data.product_key, name=data.name,
        ktru_json=json.dumps(data.ktru, ensure_ascii=False),
        attributes_json=json.dumps([a.model_dump(exclude_none=True) for a in data.attributes],
                                   ensure_ascii=False),
    )
    db.add(p)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Товар с таким product_key уже есть у компании")
    db.refresh(p)
    return _to_out(p)


@router.get("", response_model=list[ProductOut])
def list_products(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Product).filter(Product.company_id == user.company_id).all()
    return [_to_out(p) for p in rows]


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: int, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == product_id,
                                 Product.company_id == user.company_id).first()
    if p is None:  # чужой товар отдаём как 404 — не подтверждаем существование
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")
    return _to_out(p)


@router.put("/{product_id}", response_model=ProductOut)
def update_product(product_id: int, data: ProductIn, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """Обновить карточку товара (название, коды, характеристики). Изоляция по компании."""
    p = db.query(Product).filter(Product.id == product_id,
                                 Product.company_id == user.company_id).first()
    if p is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")
    p.product_key = data.product_key
    p.name = data.name
    p.ktru_json = json.dumps(data.ktru, ensure_ascii=False)
    p.attributes_json = json.dumps([a.model_dump(exclude_none=True) for a in data.attributes],
                                   ensure_ascii=False)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Товар с таким product_key уже есть у компании")
    db.refresh(p)
    return _to_out(p)
