"""Личные кабинеты: регистрация/вход компаний, сотрудники, избранное и история
сверок ТЗ — каждое личное (привязано к user_id, не к company_id). Плюс отдельная
админ-страница (HTTP Basic) со списком всех зарегистрированных компаний.

Все данные — в SQLite (server/data/lekalo.db, см. db.py). Сессия — httponly-cookie
на 30 дней, без JWT: минимальная схема, которой достаточно для одного VPS."""

from __future__ import annotations

import html
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, EmailStr

from app import auth, db, ratelimit, totp

router = APIRouter(prefix="/api")
basic = HTTPBasic()

TRIAL_DAYS = 30


# ---------- модели запросов ----------

class RegisterBody(BaseModel):
    companyName: str
    inn: str = ""
    name: str
    email: EmailStr
    password: str


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class CompanyUpdate(BaseModel):
    name: str | None = None
    inn: str | None = None


class EmployeeCreate(BaseModel):
    name: str
    email: EmailStr
    password: str


class SavedBody(BaseModel):
    purchase: dict


class TzCheckBody(BaseModel):
    purchaseId: str = ""
    purchaseNumber: str = ""
    purchaseTitle: str = ""
    score: int = 0
    verdict: str = ""


class TotpConfirmBody(BaseModel):
    code: str


class TotpDisableBody(BaseModel):
    password: str


class TotpVerifyLoginBody(BaseModel):
    tempToken: str
    code: str


# ---------- вспомогательное ----------

def _row_to_user(row) -> dict:
    return {
        "id": row["id"], "name": row["name"], "email": row["email"], "role": row["role"],
        "totpEnabled": bool(row["totp_enabled"]),
    }


def _row_to_company(row) -> dict:
    return {
        "id": row["id"], "name": row["name"], "inn": row["inn"],
        "plan": row["plan"], "planExpiresAt": row["plan_expires_at"],
    }


def _require_user(session_token: str | None):
    conn = db.get_conn()
    user = auth.get_user_by_token(conn, session_token)
    if not user:
        conn.close()
        raise HTTPException(status_code=401, detail="Не авторизован")
    return conn, user


def _norm_email(email) -> str:
    return str(email).strip().lower()


def _check_password(password: str) -> None:
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Пароль должен быть не короче 6 символов")


def _plan_label(plan: str) -> str:
    return {"start": "Тариф «Старт»", "business": "Тариф «Бизнес»", "corp": "Тариф «Корпоративный»"}.get(plan, plan)


def _check_inn_free(conn, inn: str, exclude_company_id: int | None = None) -> None:
    """ИНН — уникален на компанию (пустой ИНН не считается: его можно не указывать)."""
    if not inn:
        return
    row = conn.execute(
        "SELECT id FROM companies WHERE inn = ? AND id != ?",
        (inn, exclude_company_id or 0),
    ).fetchone()
    if row:
        raise HTTPException(status_code=409, detail="Компания с таким ИНН уже зарегистрирована")


# ---------- auth ----------

@router.post("/auth/register")
def register(body: RegisterBody, request: Request, response: Response):
    ratelimit.guard_register(ratelimit.client_ip(request))
    conn = db.get_conn()
    try:
        email = _norm_email(body.email)
        exists = conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
        if exists:
            raise HTTPException(status_code=409, detail="Компания с такой почтой уже зарегистрирована")
        _check_password(body.password)
        inn = body.inn.strip()
        _check_inn_free(conn, inn)

        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=TRIAL_DAYS)
        cur = conn.execute(
            "INSERT INTO companies (name, inn, plan, plan_expires_at, created_at) VALUES (?, ?, 'business', ?, ?)",
            (body.companyName.strip(), inn, expires.isoformat(), now.isoformat()),
        )
        company_id = cur.lastrowid
        pw_hash = auth.hash_password(body.password)
        cur = conn.execute(
            "INSERT INTO users (company_id, email, password_hash, name, role, created_at) "
            "VALUES (?, ?, ?, ?, 'owner', ?)",
            (company_id, email, pw_hash, body.name.strip(), now.isoformat()),
        )
        user_id = cur.lastrowid
        conn.commit()

        token = auth.create_session(conn, user_id)
        auth.set_session_cookie(response, token)

        user_row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        company_row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
        return {"user": _row_to_user(user_row), "company": _row_to_company(company_row)}
    finally:
        conn.close()


@router.post("/auth/login")
def login(body: LoginBody, request: Request, response: Response):
    ip = ratelimit.client_ip(request)
    email = _norm_email(body.email)
    ratelimit.guard_login(ip, email)  # 429 ДО bcrypt — режет брутфорс и CPU-DoS
    conn = db.get_conn()
    try:
        user_row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not user_row or not auth.verify_password(body.password, user_row["password_hash"]):
            ratelimit.record_login_failure(ip, email)
            raise HTTPException(status_code=401, detail="Неверная почта или пароль")
        ratelimit.reset_login(ip, email)  # верный пароль обнуляет счётчик

        if user_row["totp_enabled"]:
            # пароль верный, но нужен код 2FA — сессию пока НЕ выдаём
            token = totp.create_pending(user_row["id"])
            return {"needsTotp": True, "tempToken": token}

        token = auth.create_session(conn, user_row["id"])
        auth.set_session_cookie(response, token)

        company_row = conn.execute("SELECT * FROM companies WHERE id = ?", (user_row["company_id"],)).fetchone()
        return {"user": _row_to_user(user_row), "company": _row_to_company(company_row)}
    finally:
        conn.close()


@router.post("/auth/2fa/verify-login")
def verify_login_totp(body: TotpVerifyLoginBody, request: Request, response: Response):
    ip = ratelimit.client_ip(request)
    ratelimit.guard_totp(ip)  # 429 ДО проверки кода — 6 цифр перебираются быстро
    user_id = totp.peek_pending(body.tempToken)  # 401 если истёк/неверный; токен пока не расходуем
    conn = db.get_conn()
    try:
        user_row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user_row or not totp.verify_code(user_row["totp_secret"], body.code):
            ratelimit.record_totp_failure(ip)
            raise HTTPException(status_code=401, detail="Неверный код")
        ratelimit.reset_totp(ip)
        totp.consume_pending(body.tempToken)  # успех — токен больше не нужен

        token = auth.create_session(conn, user_row["id"])
        auth.set_session_cookie(response, token)

        company_row = conn.execute("SELECT * FROM companies WHERE id = ?", (user_row["company_id"],)).fetchone()
        return {"user": _row_to_user(user_row), "company": _row_to_company(company_row)}
    finally:
        conn.close()


@router.post("/auth/logout")
def logout(response: Response, lekalo_session: str | None = Cookie(default=None)):
    conn = db.get_conn()
    try:
        if lekalo_session:
            conn.execute("DELETE FROM sessions WHERE token = ?", (lekalo_session,))
            conn.commit()
    finally:
        conn.close()
    auth.clear_session_cookie(response)
    return {"ok": True}


@router.get("/auth/me")
def me(lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_user(lekalo_session)
    try:
        company_row = conn.execute("SELECT * FROM companies WHERE id = ?", (user["company_id"],)).fetchone()
        return {"user": _row_to_user(user), "company": _row_to_company(company_row)}
    finally:
        conn.close()


# ---------- 2FA (настройка из личного кабинета, отдельно от входа) ----------

@router.post("/auth/2fa/setup")
def setup_totp(lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_user(lekalo_session)
    try:
        # новый секрет пишем сразу, но totp_enabled остаётся 0, пока не подтвердят
        # кодом — так что незавершённая настройка не может внезапно включить 2FA
        secret = totp.generate_secret()
        conn.execute("UPDATE users SET totp_secret = ?, totp_enabled = 0 WHERE id = ?", (secret, user["id"]))
        conn.commit()
        uri = totp.provisioning_uri(secret, user["email"])
        return {"secret": secret, "otpauthUri": uri, "qrSvg": totp.qr_svg(uri)}
    finally:
        conn.close()


@router.post("/auth/2fa/confirm")
def confirm_totp(body: TotpConfirmBody, lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_user(lekalo_session)
    try:
        if not user["totp_secret"]:
            raise HTTPException(status_code=400, detail="Сначала запросите настройку 2FA")
        if not totp.verify_code(user["totp_secret"], body.code):
            raise HTTPException(status_code=400, detail="Неверный код — проверьте время на телефоне и попробуйте ещё раз")
        conn.execute("UPDATE users SET totp_enabled = 1 WHERE id = ?", (user["id"],))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.post("/auth/2fa/disable")
def disable_totp(body: TotpDisableBody, lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_user(lekalo_session)
    try:
        if not auth.verify_password(body.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Неверный пароль")
        conn.execute("UPDATE users SET totp_secret = NULL, totp_enabled = 0 WHERE id = ?", (user["id"],))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ---------- компания ----------

@router.get("/company")
def get_company(lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_user(lekalo_session)
    try:
        company_row = conn.execute("SELECT * FROM companies WHERE id = ?", (user["company_id"],)).fetchone()
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE company_id = ?", (user["company_id"],)
        ).fetchone()["n"]
        out = _row_to_company(company_row)
        out["planLabel"] = _plan_label(out["plan"])
        out["employeeCount"] = count
        return out
    finally:
        conn.close()


@router.patch("/company")
def update_company(body: CompanyUpdate, lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_user(lekalo_session)
    try:
        if user["role"] != "owner":
            raise HTTPException(status_code=403, detail="Изменять данные компании может только владелец")
        fields, params = [], []
        if body.name is not None:
            fields.append("name = ?"); params.append(body.name.strip())
        if body.inn is not None:
            inn = body.inn.strip()
            _check_inn_free(conn, inn, exclude_company_id=user["company_id"])
            fields.append("inn = ?"); params.append(inn)
        if fields:
            params.append(user["company_id"])
            conn.execute(f"UPDATE companies SET {', '.join(fields)} WHERE id = ?", params)
            conn.commit()
        company_row = conn.execute("SELECT * FROM companies WHERE id = ?", (user["company_id"],)).fetchone()
        return _row_to_company(company_row)
    finally:
        conn.close()


# ---------- сотрудники ----------

@router.get("/employees")
def list_employees(lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_user(lekalo_session)
    try:
        rows = conn.execute(
            "SELECT * FROM users WHERE company_id = ? ORDER BY created_at", (user["company_id"],)
        ).fetchall()
        return [_row_to_user(r) for r in rows]
    finally:
        conn.close()


@router.post("/employees")
def add_employee(body: EmployeeCreate, lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_user(lekalo_session)
    try:
        if user["role"] != "owner":
            raise HTTPException(status_code=403, detail="Добавлять сотрудников может только владелец")
        email = _norm_email(body.email)
        exists = conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
        if exists:
            raise HTTPException(status_code=409, detail="Такая почта уже занята")
        _check_password(body.password)
        now = datetime.now(timezone.utc).isoformat()
        pw_hash = auth.hash_password(body.password)
        cur = conn.execute(
            "INSERT INTO users (company_id, email, password_hash, name, role, created_at) "
            "VALUES (?, ?, ?, ?, 'employee', ?)",
            (user["company_id"], email, pw_hash, body.name.strip(), now),
        )
        conn.commit()
        new_row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_user(new_row)
    finally:
        conn.close()


@router.delete("/employees/{employee_id}")
def delete_employee(employee_id: int, lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_user(lekalo_session)
    try:
        if user["role"] != "owner":
            raise HTTPException(status_code=403, detail="Удалять сотрудников может только владелец")
        if employee_id == user["id"]:
            raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")
        target = conn.execute(
            "SELECT * FROM users WHERE id = ? AND company_id = ?", (employee_id, user["company_id"])
        ).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Сотрудник не найден")
        conn.execute("DELETE FROM users WHERE id = ?", (employee_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ---------- избранное (личное, привязано к user_id) ----------

@router.get("/saved")
def list_saved(lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_user(lekalo_session)
    try:
        rows = conn.execute(
            "SELECT data FROM saved_purchases WHERE user_id = ? ORDER BY created_at DESC", (user["id"],)
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]
    finally:
        conn.close()


@router.post("/saved")
def toggle_saved(body: SavedBody, lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_user(lekalo_session)
    try:
        pid = str(body.purchase.get("id") or "")
        if not pid:
            raise HTTPException(status_code=400, detail="У закупки нет id")
        existing = conn.execute(
            "SELECT id FROM saved_purchases WHERE user_id = ? AND purchase_id = ?", (user["id"], pid)
        ).fetchone()
        if existing:
            conn.execute("DELETE FROM saved_purchases WHERE id = ?", (existing["id"],))
            conn.commit()
            return {"added": False}
        conn.execute(
            "INSERT INTO saved_purchases (user_id, purchase_id, data, created_at) VALUES (?, ?, ?, ?)",
            (user["id"], pid, json.dumps(body.purchase, ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return {"added": True}
    finally:
        conn.close()


# ---------- история сверок ТЗ (личная, привязана к user_id) ----------

@router.get("/tz-checks")
def list_tz_checks(lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_user(lekalo_session)
    try:
        rows = conn.execute(
            "SELECT * FROM tz_checks WHERE user_id = ? ORDER BY created_at DESC LIMIT 100", (user["id"],)
        ).fetchall()
        return [{
            "id": r["id"], "purchaseId": r["purchase_id"], "purchaseNumber": r["purchase_number"],
            "purchaseTitle": r["purchase_title"], "score": r["score"], "verdict": r["verdict"],
            "createdAt": r["created_at"],
        } for r in rows]
    finally:
        conn.close()


@router.post("/tz-checks")
def add_tz_check(body: TzCheckBody, lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_user(lekalo_session)
    try:
        conn.execute(
            "INSERT INTO tz_checks (user_id, purchase_id, purchase_number, purchase_title, score, verdict, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user["id"], body.purchaseId, body.purchaseNumber, body.purchaseTitle, body.score, body.verdict,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ---------- админ: список всех зарегистрированных (HTTP Basic, только для владельца сайта) ----------

def _check_admin(credentials: HTTPBasicCredentials) -> None:
    admin_user = os.getenv("LK_ADMIN_USER", "admin")
    admin_pass = os.getenv("LK_ADMIN_PASS")
    if not admin_pass:
        raise HTTPException(status_code=503, detail="Админ-панель не настроена (нет LK_ADMIN_PASS)")
    ok_user = secrets.compare_digest(credentials.username, admin_user)
    ok_pass = secrets.compare_digest(credentials.password, admin_pass)
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=401, detail="Неверные учётные данные", headers={"WWW-Authenticate": "Basic"})


@router.get("/admin", response_class=HTMLResponse)
def admin_page(credentials: HTTPBasicCredentials = Depends(basic)):
    _check_admin(credentials)
    conn = db.get_conn()
    try:
        companies = conn.execute("SELECT * FROM companies ORDER BY created_at DESC").fetchall()
        rows_html = []
        for c in companies:
            users = conn.execute(
                "SELECT * FROM users WHERE company_id = ? ORDER BY role DESC, created_at", (c["id"],)
            ).fetchall()
            owner = next((u for u in users if u["role"] == "owner"), None)
            users_html = "".join(
                f"<li>{'★ ' if u['role'] == 'owner' else ''}{html.escape(u['name'])} — {html.escape(u['email'])} "
                f"<span style='color:#888'>(#{u['id']})</span></li>"
                for u in users
            )
            rows_html.append(f"""
              <tr>
                <td>#{c['id']}</td>
                <td>{html.escape(c['name'])}<br><span style="color:#888;font-size:.85em">ИНН {html.escape(c['inn'] or '—')}</span></td>
                <td>{html.escape(owner['email']) if owner else '—'}</td>
                <td>{_plan_label(c['plan'])}<br><span style="color:#888;font-size:.85em">до {c['plan_expires_at'][:10]}</span></td>
                <td>{len(users)}</td>
                <td><ul style="margin:0;padding-left:18px;">{users_html}</ul></td>
                <td style="color:#888;font-size:.85em">{c['created_at'][:16].replace('T',' ')}</td>
              </tr>""")
        page_html = f"""
        <html><head><meta charset="utf-8"><title>Лекало — зарегистрированные компании</title>
        <style>
          body {{ font-family: -apple-system, Segoe UI, sans-serif; margin: 30px; color: #222; }}
          table {{ border-collapse: collapse; width: 100%; }}
          th, td {{ border: 1px solid #ddd; padding: 8px 10px; text-align: left; vertical-align: top; font-size: .92rem; }}
          th {{ background: #f4f1ea; }}
          h1 {{ font-size: 1.3rem; }}
        </style></head>
        <body>
          <h1>Зарегистрированные компании ({len(companies)})</h1>
          <table>
            <tr><th>№</th><th>Компания</th><th>Email владельца</th><th>Тариф</th><th>Сотрудников</th><th>Список</th><th>Регистрация</th></tr>
            {''.join(rows_html) or '<tr><td colspan="7">Пока никто не зарегистрировался</td></tr>'}
          </table>
        </body></html>"""
        return HTMLResponse(page_html)
    finally:
        conn.close()
