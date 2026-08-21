"""Счета на оплату по реквизитам — безналичный расчёт, без эквайринга.

Клиент выставляет себе счёт из кабинета (тариф → сумма → печатная HTML-
страница с реквизитами обеих сторон). Оплата — банковским переводом вне
сайта; отметка "оплачено" — вручную в админке (см. /api/admin в
accounts.py), после чего у компании продлевается plan_expires_at.

Реквизиты получателя — константы ниже, не секрет: они и так печатаются на
каждом счёте.
"""

from __future__ import annotations

import html
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasicCredentials
from pydantic import BaseModel

from app import audit, db, ratelimit
from app.accounts import _check_admin, _plan_label, _require_user, basic
from app.telegram import send_message


def _guard_admin(request: Request, credentials: HTTPBasicCredentials) -> None:
    """Тот же бруфорс-щит и аудит, что у /api/admin (accounts.py) — счета
    открывают не менее секретный доступ к плательщикам, ослаблять нельзя."""
    ip = ratelimit.client_ip(request)
    ratelimit.guard_admin(ip)
    try:
        _check_admin(credentials)
    except HTTPException:
        ratelimit.record_admin_failure(ip)
        audit.log("admin_fail", ip=ip, user=credentials.username)
        raise
    ratelimit.reset_admin(ip)
    audit.log("admin_access", ip=ip)

router = APIRouter(prefix="/api")

PAYEE = {
    "name": "ООО «ШИРОТА-66»",
    "inn": "9709099892",
    "kpp": "770901001",
    "ogrn": "1237700674639",
    "account": "40702810810001504717",
    "bank": "АО «Тинькофф Банк»",
    "bik": "044525974",
    "bank_inn": "7710140679",
    "corr_account": "30101810145250000974",
    "address": "109004, Россия, г. Москва, пер. Товарищеский, д. 8, стр. 2, помещ. 1/1",
    "bank_address": "127287, г. Москва, ул. Хуторская 2-я, д. 38А, стр. 26",
}

# "Корп" — по запросу, сумма не фиксирована, поэтому самостоятельно не
# выставляется: за ним отправляем в поддержку (см. app/support.py).
PLAN_PRICES = {"start": 15000, "business": 35000}
VAT_RATE = 0.05  # НДС 5% (пониженная ставка УСН) — сумма в счёте включает его


class InvoiceCreate(BaseModel):
    plan: str


def _vat(amount: int) -> int:
    return round(amount * VAT_RATE / (1 + VAT_RATE))


def _invoice_number(invoice_id: int, created_at: str) -> str:
    ym = created_at[:7].replace("-", "")
    return f"ЛК-{ym}-{invoice_id}"


def _money(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def create_invoice_for_company(conn, company_id: int, plan: str) -> dict:
    """Общая логика выставления счёта — используется и кабинетом (POST
    /invoices ниже), и Telegram-ботом (кнопки тарифов, см. app/support.py).
    conn НЕ закрывает — вызывающий код владеет соединением."""
    if plan not in PLAN_PRICES:
        raise HTTPException(status_code=400, detail="Этот тариф выставляется по запросу — напишите в поддержку")
    amount = PLAN_PRICES[plan]
    now = datetime.now(timezone.utc).isoformat()
    access_token = secrets.token_urlsafe(24)
    cur = conn.execute(
        "INSERT INTO invoices (company_id, plan, amount, vat, status, created_at, access_token) "
        "VALUES (?, ?, ?, ?, 'unpaid', ?, ?)",
        (company_id, plan, amount, _vat(amount), now, access_token),
    )
    conn.commit()
    return {"id": cur.lastrowid, "number": _invoice_number(cur.lastrowid, now), "accessToken": access_token}


@router.post("/invoices")
def create_invoice(body: InvoiceCreate, lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_user(lekalo_session)
    try:
        result = create_invoice_for_company(conn, user["company_id"], body.plan)
        return {"id": result["id"], "number": result["number"]}
    finally:
        conn.close()


@router.get("/invoices")
def list_invoices(lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_user(lekalo_session)
    try:
        rows = conn.execute(
            "SELECT * FROM invoices WHERE company_id = ? ORDER BY created_at DESC", (user["company_id"],)
        ).fetchall()
        return [{
            "id": r["id"], "number": _invoice_number(r["id"], r["created_at"]),
            "plan": r["plan"], "planLabel": _plan_label(r["plan"]),
            "amount": r["amount"], "status": r["status"],
            "createdAt": r["created_at"], "paidAt": r["paid_at"],
        } for r in rows]
    finally:
        conn.close()


def _invoice_html(inv, company) -> str:
    esc = html.escape
    number = _invoice_number(inv["id"], inv["created_at"])
    date = inv["created_at"][:10]
    paid_stamp = (
        '<div style="color:#3f6a45;font-weight:700;font-size:1.3rem;margin-bottom:14px;">✓ ОПЛАЧЕНО</div>'
        if inv["status"] == "paid" else ""
    )
    payer_inn = f", ИНН {esc(company['inn'])}" if company["inn"] else ""
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Счёт № {esc(number)}</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", sans-serif; max-width: 760px; margin: 40px auto; color: #222; font-size: 14px; }}
  h1 {{ font-size: 1.15rem; }}
  table {{ border-collapse: collapse; width: 100%; margin: 14px 0; }}
  td, th {{ border: 1px solid #999; padding: 6px 10px; text-align: left; }}
  .req {{ font-size: 0.85rem; line-height: 1.5; margin-bottom: 4px; }}
  .total {{ font-weight: 700; }}
  @media print {{ body {{ margin: 0; }} }}
</style></head><body>
{paid_stamp}
<h1>Счёт на оплату № {esc(number)} от {date}</h1>

<p class="req"><b>Поставщик:</b> {esc(PAYEE['name'])}, ИНН {PAYEE['inn']}, КПП {PAYEE['kpp']}, ОГРН {PAYEE['ogrn']}<br>
Адрес: {esc(PAYEE['address'])}<br>
Р/с {PAYEE['account']} в {esc(PAYEE['bank'])}, БИК {PAYEE['bik']}, ИНН банка {PAYEE['bank_inn']}<br>
К/с {PAYEE['corr_account']}, {esc(PAYEE['bank_address'])}</p>

<p class="req"><b>Покупатель:</b> {esc(company['name'])}{payer_inn}</p>

<table>
  <tr><th>№</th><th>Наименование</th><th>Кол-во</th><th>Цена, ₽</th><th>Сумма, ₽</th></tr>
  <tr><td>1</td><td>Подписка «Лекало», {esc(_plan_label(inv['plan']))}, 1 месяц</td><td>1</td>
      <td>{_money(inv['amount'])}</td><td>{_money(inv['amount'])}</td></tr>
</table>

<p class="total">Итого: {_money(inv['amount'])} ₽, в т.ч. НДС 5%: {_money(inv['vat'])} ₽</p>

<p style="color:#666;font-size:0.85rem;margin-top:26px;">Оплата — по указанным реквизитам. Услуга считается
оплаченной после поступления средств на счёт поставщика.</p>
</body></html>"""


@router.get("/invoices/{invoice_id}", response_class=HTMLResponse)
def view_invoice(invoice_id: int, lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_user(lekalo_session)
    try:
        inv = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if not inv or inv["company_id"] != user["company_id"]:
            raise HTTPException(status_code=404, detail="Счёт не найден")
        company = conn.execute("SELECT * FROM companies WHERE id = ?", (inv["company_id"],)).fetchone()
        return HTMLResponse(_invoice_html(inv, company))
    finally:
        conn.close()


@router.get("/invoices/{invoice_id}/bot", response_class=HTMLResponse)
def view_invoice_from_bot(invoice_id: int, t: str = ""):
    """Тот же счёт, но без cookie-сессии — ссылку присылает бот (см.
    app/support.py), а у Telegram-браузера сайтовой сессии нет. Доступ —
    по одноразовому access_token, выданному при создании счёта."""
    conn = db.get_conn()
    try:
        inv = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if not inv or not t or not secrets.compare_digest(inv["access_token"] or "", t):
            raise HTTPException(status_code=404, detail="Счёт не найден")
        company = conn.execute("SELECT * FROM companies WHERE id = ?", (inv["company_id"],)).fetchone()
        return HTMLResponse(_invoice_html(inv, company))
    finally:
        conn.close()


# ---------- админ: список счетов + отметка оплаты (HTTP Basic, см. accounts.py) ----------
# Отдельная страница, не встроенная в /api/admin — accounts.py не импортирует
# этот модуль (импорт был бы обратным кругом: invoices.py и так берёт
# _check_admin/basic ОТТУДА), поэтому просто перелинкованы между собой.

@router.get("/admin/invoices", response_class=HTMLResponse)
def admin_invoices_page(request: Request, credentials: HTTPBasicCredentials = Depends(basic)):
    _guard_admin(request, credentials)
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT invoices.*, companies.name AS company_name, companies.inn AS company_inn "
            "FROM invoices JOIN companies ON companies.id = invoices.company_id "
            "ORDER BY (invoices.status = 'unpaid') DESC, invoices.created_at DESC"
        ).fetchall()
        rows_html = []
        for r in rows:
            paid = r["status"] == "paid"
            action = (
                f"оплачен {r['paid_at'][:10]}" if paid
                else f'<a href="/api/admin/invoices/{r["id"]}/pay">Отметить оплаченным</a>'
            )
            rows_html.append(f"""
              <tr>
                <td>{html.escape(_invoice_number(r['id'], r['created_at']))}</td>
                <td>{html.escape(r['company_name'])}<br><span style="color:#888;font-size:.85em">ИНН {html.escape(r['company_inn'] or '—')}</span></td>
                <td>{html.escape(_plan_label(r['plan']))}</td>
                <td>{_money(r['amount'])} ₽</td>
                <td>{r['created_at'][:10]}</td>
                <td>{'✓ оплачен' if paid else action}</td>
              </tr>""")
        page_html = f"""
        <html><head><meta charset="utf-8"><title>Лекало — счета</title>
        <style>
          body {{ font-family: -apple-system, Segoe UI, sans-serif; margin: 30px; color: #222; }}
          table {{ border-collapse: collapse; width: 100%; }}
          th, td {{ border: 1px solid #ddd; padding: 8px 10px; text-align: left; vertical-align: top; font-size: .92rem; }}
          th {{ background: #f4f1ea; }}
          h1 {{ font-size: 1.3rem; }}
          a {{ color: #b3401f; }}
        </style></head>
        <body>
          <h1>Счета ({len(rows)}) · <a href="/api/admin">← компании</a></h1>
          <table>
            <tr><th>№ счёта</th><th>Компания</th><th>Тариф</th><th>Сумма</th><th>Выставлен</th><th>Статус</th></tr>
            {''.join(rows_html) or '<tr><td colspan="6">Пока нет счетов</td></tr>'}
          </table>
        </body></html>"""
        return HTMLResponse(page_html)
    finally:
        conn.close()


@router.get("/admin/invoices/{invoice_id}/pay")
async def admin_mark_paid(invoice_id: int, request: Request, credentials: HTTPBasicCredentials = Depends(basic)):
    _guard_admin(request, credentials)
    conn = db.get_conn()
    try:
        inv = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if not inv:
            raise HTTPException(status_code=404, detail="Счёт не найден")
        if inv["status"] != "paid":
            now = datetime.now(timezone.utc)
            conn.execute("UPDATE invoices SET status='paid', paid_at=? WHERE id=?", (now.isoformat(), invoice_id))
            company = conn.execute("SELECT * FROM companies WHERE id=?", (inv["company_id"],)).fetchone()
            # Продлеваем от текущего окончания подписки, а не от "сейчас" — иначе
            # оплата чуть заранее срезала бы уже оплаченный остаток месяца.
            cur_expiry = datetime.fromisoformat(company["plan_expires_at"])
            new_expiry = max(cur_expiry, now) + timedelta(days=30)
            conn.execute(
                "UPDATE companies SET plan=?, plan_expires_at=? WHERE id=?",
                (inv["plan"], new_expiry.isoformat(), inv["company_id"]),
            )
            conn.commit()
            audit.log("invoice_paid", invoice=invoice_id, company=inv["company_id"], plan=inv["plan"], amount=inv["amount"])
            owner = conn.execute(
                "SELECT telegram_chat_id FROM users WHERE company_id = ? AND role = 'owner'",
                (inv["company_id"],),
            ).fetchone()
            if owner and owner["telegram_chat_id"]:
                await send_message(
                    owner["telegram_chat_id"],
                    f"Оплата получена ✅ Тариф «{_plan_label(inv['plan'])}» активен до "
                    f"{new_expiry.date().isoformat()}. Спасибо!",
                )
        return RedirectResponse(url="/api/admin/invoices", status_code=303)
    finally:
        conn.close()
