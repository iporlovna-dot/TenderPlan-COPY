"""Беспилотный воркер: обновляет ленту компаний (Этап 1 — «беспилотный конвейер»).

Оживляет ленту: берёт товары компании из БД → выгружает их как карточки → гоняет
проверенный автономный конвейер `run_auto` (фид → воронка → извлечение → align → match) →
кладёт свежие вердикты в БД через `api.ingest`. Ничего нового в логике матчинга: воркер
только оркестрирует уже работающие куски.

Single-shot (для cron) или цикл (`--interval`). Ключи (TENDERPLAN_TOKEN, ANTHROPIC_API_KEY)
грузятся из .env. БД — та же, что у API (SPECMATCH_DATABASE_URL / по умолчанию data/app.db).

    # один прогон для компании 1 по профилю ларингоскопов, до 5 закупок
    .venv/bin/python scripts/worker.py --company 1 --profile data/profiles/laryngoscope.json --limit 5
    # в цикле раз в 6 часов (cron-замена на время разработки)
    .venv/bin/python scripts/worker.py --company 1 --profile ... --interval 21600
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _load_env():
    envp = os.path.join(_ROOT, ".env")
    if os.path.exists(envp):
        for line in open(envp):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _export_cards(products, dest):
    """Выгрузить карточки товаров компании в JSON (id = product_key, чтобы ingest сопоставил)."""
    for p in products:
        card = {"id": p.product_key, "name": p.name,
                "ktru": json.loads(p.ktru_json or "[]"),
                "attributes": json.loads(p.attributes_json or "[]")}
        json.dump(card, open(os.path.join(dest, p.product_key + ".json"), "w", encoding="utf-8"),
                  ensure_ascii=False)


def refresh_company(db, company_id: int, profile_path: str, limit: int) -> int:
    from api.models import Product
    from api.ingest import ingest_results

    products = db.query(Product).filter(Product.company_id == company_id).all()
    if not products:
        print("  компания %d: нет товаров — пропуск" % company_id)
        return 0
    with tempfile.TemporaryDirectory() as cards, tempfile.TemporaryDirectory() as out:
        _export_cards(products, cards)
        cmd = [sys.executable, os.path.join(_ROOT, "scripts", "run_auto.py"),
               profile_path, cards, "--out", out, "--limit", str(limit)]
        subprocess.run(cmd, cwd=_ROOT, env=os.environ, check=False)
        n = ingest_results(db, out, company_id)
    db.commit()
    return n


def main():
    ap = argparse.ArgumentParser(description="Беспилотный воркер обновления ленты")
    ap.add_argument("--company", type=int, required=True, help="id компании")
    ap.add_argument("--profile", required=True, help="профиль категории для поиска в фиде")
    ap.add_argument("--limit", type=int, default=5, help="макс. закупок за прогон")
    ap.add_argument("--interval", type=int, default=0, help="сек между прогонами (0 — один раз)")
    args = ap.parse_args()

    _load_env()
    from api.db import SessionLocal, init_db
    init_db()

    while True:
        db = SessionLocal()
        try:
            n = refresh_company(db, args.company, args.profile, args.limit)
            print("[%s] компания %d: обновлено лидов %d" %
                  (time.strftime("%H:%M:%S"), args.company, n))
        finally:
            db.close()
        if args.interval <= 0:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
