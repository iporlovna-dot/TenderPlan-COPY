"""Демо-посев БД MVP: компания + пользователь + настоящая карточка BESDATA + реальная лента.

Заводит всё детерминированно и идемпотентно (повторный запуск не плодит дубли) — чтобы
увидеть населённую ленту в Swagger/кабинете БЕЗ ручного ввода. Карточка берётся из
`data/products/videolaryngoscope_besdata_bd_df.json` (не печатается руками). Лиды — РЕАЛЬНЫЕ
закупки этой сессии с фактическими % (замерены движком). Это демо-shortcut; штатный путь
наполнения ленты — конвейер `run_auto` → `api/ingest`.

    .venv/bin/python scripts/seed_demo.py
    # затем в Swagger: POST /auth/login {demo@specmatch.local / demo-pass-12345} → Authorize
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.db import SessionLocal, init_db  # noqa: E402
from api.models import Company, Lead, Product, User  # noqa: E402
from api.security import hash_password  # noqa: E402

DEMO_EMAIL = "demo@specmatch.ru"
DEMO_PASSWORD = "demo-pass-12345"
CARD_PATH = os.path.join(os.path.dirname(__file__), "..",
                         "data", "products", "videolaryngoscope_besdata_bd_df.json")

# Реальные закупки сессии (id/предмет/заказчик/НМЦК/вердикт/%) — фактические результаты движка.
SESSION_LEADS = [
    ("6856b7b15d48d39bb4ebe08a", "Ларингоскоп интубационный жёсткий, многоразового использования",
     'ГБУ "КОДКБ им. Красного Креста" (Курган)', 510000.0, "45", "eligible", 100),
    ("6905888d5d48d39bb41decaf", "Ларингоскоп интубационный жёсткий, многоразового использования",
     'КГБУЗ "АКОД" (Барнаул)', 450000.0, "22", "eligible_with_gaps", 97),
    ("68e501765d48d39bb49c0b58", "Ларингоскоп интубационный жёсткий, многоразового использования",
     'КГБУЗ "Городская больница №8" (Барнаул)', 450000.0, "22", "eligible_with_gaps", 97),
    ("6a61fecc3951804ff361a021", "Ларингоскоп интубационный жёсткий, многоразового использования",
     'БУЗОО "ОКБ" (Омск)', 410000.0, "55", "eligible_with_gaps", 88),
    ("6900dbc95d48d39bb4266308", "Ларингоскоп интубационный жёсткий, многоразового использования",
     'ГУЗ "НГКБ" (Новомосковск)', 897000.0, "71", "eligible_with_gaps", 85),
    ("68c3a6bb5d48d39bb4a03ee9", "Поставка системы для видеоларингоскопии",
     'ГУЗ "СГКБ №6 им. Кошелева" (Саратов)', 495400.0, "64", "eligible_with_gaps", 78),
    ("6a6372cf3951804ff3204a9f", "Ларингоскоп интубационный жёсткий, многоразового использования",
     'ГУЗ "КБ СМП №7"', 489510.0, "64", "eligible_with_gaps", 50),
]


def _target_company(db, email):
    """Компания, в которую сеем. Если задан email существующего пользователя — его компания
    (увидит под своим логином). Иначе — демо-пользователь demo@specmatch.ru / demo-pass-12345."""
    if email:
        user = db.query(User).filter(User.email == email.lower().strip()).first()
        if user is None:
            raise SystemExit("Пользователь %s не найден — сначала зарегистрируйся в /docs" % email)
        return db.query(Company).filter(Company.id == user.company_id).first(), None
    user = db.query(User).filter(User.email == DEMO_EMAIL).first()
    if user:
        return db.query(Company).filter(Company.id == user.company_id).first(), DEMO_EMAIL
    company = Company(name="BESDATA Demo (демо-поставщик)")
    db.add(company)
    db.flush()
    db.add(User(email=DEMO_EMAIL, password_hash=hash_password(DEMO_PASSWORD),
                company_id=company.id))
    return company, DEMO_EMAIL


def _upsert_product(db, company) -> Product:
    card = json.load(open(CARD_PATH, encoding="utf-8"))
    key = card["id"]
    p = (db.query(Product)
         .filter(Product.company_id == company.id, Product.product_key == key).first())
    if p is None:
        p = Product(company_id=company.id, product_key=key)
        db.add(p)
    p.name = card["name"]
    p.ktru_json = json.dumps(card.get("ktru", []), ensure_ascii=False)
    p.attributes_json = json.dumps(card.get("attributes", []), ensure_ascii=False)
    db.flush()
    return p


def _upsert_leads(db, company, product) -> int:
    n = 0
    for pid, subj, cust, price, region, verdict, score in SESSION_LEADS:
        lead = (db.query(Lead)
                .filter(Lead.product_id == product.id, Lead.purchase_id == pid).first())
        if lead is None:
            lead = Lead(company_id=company.id, product_id=product.id, purchase_id=pid)
            db.add(lead)
        lead.subject, lead.customer, lead.price = subj, cust, price
        lead.region, lead.verdict, lead.score = region, verdict, score
        lead.url = "https://tenderplan.ru/app?tender=%s" % pid
        lead.explanation = "Демо-посев: реальный результат сессии."
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description="Демо-посев БД MVP")
    ap.add_argument("--email", help="привязать к существующему пользователю (его компания)")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        company, demo_login = _target_company(db, args.email)
        product = _upsert_product(db, company)
        n = _upsert_leads(db, company, product)
        db.commit()
        print("✓ Демо-посев готов.")
        print("  Компания:", company.name, "| товар:", product.name[:50], "(id=%d)" % product.id)
        print("  Лидов в ленте:", n)
        if demo_login:
            print("\nВойти в Swagger (/docs → POST /auth/login):")
            print("  email:   ", DEMO_EMAIL)
            print("  password:", DEMO_PASSWORD)
        else:
            print("\nВойди под своим аккаунтом (%s) — товар и лента уже у тебя." % args.email)
        print("Затем Authorize токеном → GET /products/%d/leads?min_score=60" % product.id)
    finally:
        db.close()


if __name__ == "__main__":
    main()
