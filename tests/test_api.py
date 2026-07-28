"""Интеграционные тесты MVP-API: аутентификация, изоляция компаний, товары, лента.
Временная SQLite-БД, без сети. Проверяет secure-by-design поведение (единый ответ,
rate-limit) и мультиарендную изоляцию."""
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp()
os.environ["SPECMATCH_DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "test.db")
os.environ["SPECMATCH_SECRET_KEY"] = "test-secret-key-fixed-0123456789abcdef-long-enough"
os.environ["SPECMATCH_LOGIN_MAX_FAILS"] = "5"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from api.db import SessionLocal, init_db  # noqa: E402
from api.ingest import ingest_results  # noqa: E402
from api.models import Product  # noqa: E402
from api.main import app  # noqa: E402

init_db()
client = TestClient(app)


def check(name, ok):
    print(("  ✓ " if ok else "  ✗ ") + name)
    return ok


def _auth(token):
    return {"Authorization": "Bearer " + token}


def run():
    r = []

    # регистрация двух компаний
    ra = client.post("/auth/register", json={"email": "a@x.io", "password": "supersecret1",
                                             "company_name": "Компания A"})
    rb = client.post("/auth/register", json={"email": "b@x.io", "password": "supersecret2",
                                             "company_name": "Компания B"})
    r.append(check("регистрация A → 201", ra.status_code == 201))
    r.append(check("регистрация B → 201", rb.status_code == 201))
    tok_a, tok_b = ra.json()["access_token"], rb.json()["access_token"]

    r.append(check("повторный email → 409",
                   client.post("/auth/register", json={"email": "a@x.io", "password": "supersecret1",
                                                       "company_name": "X"}).status_code == 409))
    r.append(check("короткий пароль → 422",
                   client.post("/auth/register", json={"email": "c@x.io", "password": "short",
                                                       "company_name": "X"}).status_code == 422))

    # товар компании A
    prod = {"product_key": "besdata-1", "name": "Видеоларингоскоп BESDATA",
            "ktru": ["32.50.13.190-00007689"],
            "attributes": [{"key": "источник_света", "value": "LED"}]}
    cp = client.post("/products", json=prod, headers=_auth(tok_a))
    r.append(check("A создаёт товар → 201", cp.status_code == 201))
    pid = cp.json()["id"]

    r.append(check("A видит 1 товар", len(client.get("/products", headers=_auth(tok_a)).json()) == 1))
    r.append(check("B видит 0 товаров (изоляция)",
                   len(client.get("/products", headers=_auth(tok_b)).json()) == 0))
    r.append(check("B не видит товар A → 404",
                   client.get("/products/%d" % pid, headers=_auth(tok_b)).status_code == 404))
    r.append(check("без токена → 401", client.get("/products").status_code == 401))

    # логин: неверный пароль → единый 401; верный → 200
    r.append(check("логин неверный пароль → 401",
                   client.post("/auth/login", json={"email": "a@x.io", "password": "wrong"}).status_code == 401))
    r.append(check("логин верный → 200",
                   client.post("/auth/login", json={"email": "a@x.io", "password": "supersecret1"}).status_code == 200))
    r.append(check("логин несуществующий email → 401 (без утечки)",
                   client.post("/auth/login", json={"email": "nope@x.io", "password": "whatever12"}).status_code == 401))

    # лента: ingest вердикта run_auto → GET leads
    db = SessionLocal()
    p = db.query(Product).filter(Product.product_key == "besdata-1").first()
    cid = p.company_id
    rdir = os.path.join(_TMP, "results")
    os.makedirs(rdir, exist_ok=True)
    json.dump({"purchase_id": "6a6372cf", "subject": "Ларингоскоп интубационный жёсткий",
               "product_id": "besdata-1", "score": 88, "verdict": "eligible_with_gaps",
               "explanation": "ok", "customer": "ГУЗ КБ СМП №7", "price": 489510.0,
               "region": "64", "submission_close": None,
               "url": "https://tenderplan.ru/app?tender=6a6372cf",
               # требования для дозаполнения пробелов: источник_света пройдёт, угол_обзора — пробел
               "requirements": [
                   {"key": "источник_света", "operator": "eq", "value": "LED", "unit": "",
                    "hardness": "soft", "type": "technical", "raw": "Источник света - LED"},
                   {"key": "угол_обзора", "operator": "gte", "value": 60, "unit": "°",
                    "hardness": "soft", "type": "technical", "raw": "Угол обзора не менее 60"}],
               "synonyms": {},
               # наглядная карточка контракта
               "card": {"reg_number": "0358300081226000236", "href": "https://zakupki.gov.ru/x",
                        "customer": "ГУЗ КБ СМП №7", "nmck": 489510.0, "region": "64",
                        "delivery_place": "г. Саратов", "placing_way": "15", "smp": False,
                        "publication_date": 1785233243000, "submission_start": 1785233245138,
                        "submission_close": 1785909600000, "bidding_date": 1785916800000,
                        "summing_up_date": 1786050000000, "guarantee_app": None,
                        "guarantee_contract": 21363.504, "prepayment": None}},
              open(os.path.join(rdir, "v1.json"), "w"))
    n = ingest_results(db, rdir, cid)
    db.close()
    r.append(check("ingest загрузил 1 вердикт", n == 1))

    leads = client.get("/products/%d/leads?min_score=60" % pid, headers=_auth(tok_a))
    r.append(check("A видит лид 88%", leads.status_code == 200 and len(leads.json()) == 1
                   and leads.json()[0]["score"] == 88))

    # карточка контракта в ленте (§Этап 1)
    card = leads.json()[0].get("card")
    r.append(check("лид несёт карточку контракта", card is not None))
    r.append(check("реестровый номер в карточке", card and card["reg_number"] == "0358300081226000236"))
    r.append(check("обеспечение контракта в карточке", card and card["guarantee_contract"] == 21363.504))
    r.append(check("дата торгов в карточке", card and card["bidding_date"] == 1785916800000))
    r.append(check("дата подведения итогов в карточке", card and card["summing_up_date"] == 1786050000000))
    r.append(check("дней до подачи рассчитано", card and card["days_to_submission"] is not None))
    r.append(check("B не видит ленту товара A → 404",
                   client.get("/products/%d/leads" % pid, headers=_auth(tok_b)).status_code == 404))

    # --- Дозаполнение пробелов (§Этап 1) ---
    det = client.get("/products/%d/leads/6a6372cf" % pid, headers=_auth(tok_a))
    r.append(check("деталь лида → 200", det.status_code == 200))
    r.append(check("угол_обзора — пробел (gap)", "угол_обзора" in det.json()["gaps"]))
    r.append(check("источник_света прошёл",
                   any(c["req"] == "источник_света" and c["status"] == "pass"
                       for c in det.json()["checks"])))
    score0 = det.json()["score"]

    # честность: значение НИЖЕ порога не «зачитывается»
    bad = client.post("/products/%d/leads/6a6372cf/fill" % pid,
                      json={"fills": {"угол_обзора": 50}}, headers=_auth(tok_a))
    r.append(check("дозаполнение 50 (<60) не проходит честно",
                   not any(c["req"] == "угол_обзора" and c["status"] == "pass"
                           for c in bad.json()["checks"])))

    # валидное значение → пробел закрыт, % вырос
    ok = client.post("/products/%d/leads/6a6372cf/fill" % pid,
                     json={"fills": {"угол_обзора": 70}}, headers=_auth(tok_a))
    r.append(check("дозаполнение 70 (≥60) → % вырос", ok.json()["score"] > score0))
    r.append(check("угол_обзора больше не пробел", "угол_обзора" not in ok.json()["gaps"]))
    r.append(check("значение вписано в карточку (confirmable)",
                   any(a["key"] == "угол_обзора" and a["status"] == "confirmable"
                       for a in ok.json()["attributes"])))
    r.append(check("B не может дозаполнить лид A → 404",
                   client.post("/products/%d/leads/6a6372cf/fill" % pid,
                               json={"fills": {"угол_обзора": 70}}, headers=_auth(tok_b)).status_code == 404))

    # rate-limit: 5 неудач по свежему email → блокировка (429)
    for _ in range(5):
        client.post("/auth/login", json={"email": "ratelimit@x.io", "password": "bad12345"})
    r.append(check("после 5 неудач → 429",
                   client.post("/auth/login", json={"email": "ratelimit@x.io", "password": "bad12345"}).status_code == 429))
    return r


if __name__ == "__main__":
    res = run()
    passed = sum(res)
    print("\n%d/%d passed" % (passed, len(res)))
    sys.exit(0 if passed == len(res) else 1)
