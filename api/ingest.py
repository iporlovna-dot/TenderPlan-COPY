"""Мост CLI→API: загрузка вердиктов run_auto (--out/*.json) в БД как Lead'ы.

Так проверенный конвейер (discover→фильтр→извлечение→match→вердикты) питает ленту API,
не смешиваясь с ним. Запуск воркером/cron после run_auto.

    .venv/bin/python -m api.ingest --results data/results --company 1
"""
from __future__ import annotations

import argparse
import glob
import json
import os

from sqlalchemy.orm import Session

from api.db import SessionLocal, init_db
from api.models import Lead, Product


def ingest_results(db: Session, results_dir: str, company_id: int) -> int:
    """Загрузить вердикты из папки в Lead'ы компании. Товар связывается по product_key
    (`product_id` в JSON вердикта = `Product.product_key`). Возвращает число upsert'ов."""
    by_key = {p.product_key: p for p in
              db.query(Product).filter(Product.company_id == company_id).all()}
    n = 0
    for fp in glob.glob(os.path.join(results_dir, "*.json")):
        v = json.load(open(fp, encoding="utf-8"))
        product = by_key.get(v.get("product_id"))
        if product is None:
            continue  # нет такого товара у компании — пропускаем
        lead = (db.query(Lead)
                .filter(Lead.product_id == product.id, Lead.purchase_id == v["purchase_id"])
                .first())
        if lead is None:
            lead = Lead(company_id=company_id, product_id=product.id, purchase_id=v["purchase_id"])
            db.add(lead)
        lead.subject = v.get("subject", "")
        lead.customer = v.get("customer", "") or ""
        lead.price = v.get("price")
        lead.region = v.get("region")
        lead.submission_close = v.get("submission_close")
        lead.url = v.get("url", "")
        lead.score = int(v.get("score", 0))
        lead.verdict = v.get("verdict", "")
        lead.explanation = v.get("explanation", "")
        n += 1
    db.commit()
    return n


def main():
    ap = argparse.ArgumentParser(description="Ingest вердиктов run_auto в ленту API")
    ap.add_argument("--results", default="data/results")
    ap.add_argument("--company", type=int, required=True, help="id компании")
    args = ap.parse_args()
    init_db()
    db = SessionLocal()
    try:
        print("Загружено вердиктов:", ingest_results(db, args.results, args.company))
    finally:
        db.close()


if __name__ == "__main__":
    main()
