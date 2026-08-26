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

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, EmailStr

from app import audit, auth, db, ratelimit, totp
from app.telegram import BOT_USERNAME

router = APIRouter(prefix="/api")
basic = HTTPBasic()

DEMO_DAYS = 3
# автопродление (см. _require_active_user / admin_grant_business) ставит
# expires далеко в будущее — само значение неважно, доступ держит auto_renew=1,
# но дата всё равно нужна (колонка NOT NULL) и полезна как читаемая метка в админке
AUTO_RENEW_YEARS_AHEAD = 10

# Лимит сотрудников на компанию по тарифу (одно место — тарифные возможности
# ещё будут меняться). demo = только владелец; >10 = отдельная компания (см.
# «группа компаний», admin link). Неизвестный план откатывается к самому строгому.
PLAN_EMPLOYEE_LIMITS = {"demo": 1, "start": 2, "business": 5, "corp": 10}
DEFAULT_EMPLOYEE_LIMIT = 2


def _employee_limit(plan: str) -> int:
    return PLAN_EMPLOYEE_LIMITS.get(plan, DEFAULT_EMPLOYEE_LIMIT)


# Лимит сохранённых поисков НА ПОЛЬЗОВАТЕЛЯ по тарифу (None = без лимита).
# Совпадает с тарифами на лендинге и в terms.html. Демо — 3 (см. фронт accessLimits).
# Фронт (app.js accessLimits) держит те же числа — сервер тут последний рубеж
# (лимит на фронте обходится прямым вызовом API, см. todo-demo-limits-backend).
PLAN_SEARCH_LIMITS = {"demo": 3, "start": 5, "business": None, "corp": None}
DEFAULT_SEARCH_LIMIT = 3


def _search_limit(plan: str):
    return PLAN_SEARCH_LIMITS.get(plan, DEFAULT_SEARCH_LIMIT)


# Лимит карточек товара НА ПОЛЬЗОВАТЕЛЯ по тарифу (None = без лимита). Совпадает
# с фронтом (app.js PLAN_PRODUCT_LIMITS). Демо — 1 (попробовать движок), Старт — 5,
# Бизнес — 25, Корпоративный — без лимита.
PLAN_PRODUCT_LIMITS = {"demo": 1, "start": 5, "business": 25, "corp": None}
DEFAULT_PRODUCT_LIMIT = 1


def _product_limit(plan: str):
    return PLAN_PRODUCT_LIMITS.get(plan, DEFAULT_PRODUCT_LIMIT)


# ---------- модели запросов ----------

class RegisterBody(BaseModel):
    companyName: str
    inn: str = ""
    name: str
    email: EmailStr
    password: str
    consentPdn: bool = False       # согласие на обработку ПДн — обязательное
    consentMarketing: bool = False  # согласие на рассылку в Telegram — по желанию


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


# статусы воронки закупки на общей доске компании (порядок = порядок в UI)
BOARD_STATUSES = ["Интересно", "Участвуем", "В просчёте", "Заключён контракт", "Исполнен", "Ждём оплату"]


class SavedBody(BaseModel):
    purchase: dict
    status: str | None = None


class BoardPatch(BaseModel):
    status: str | None = None
    assignee_id: int | None = None


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


class SearchCreate(BaseModel):
    id: str  # клиент генерирует (как и раньше в localStorage) — проще синхронного создания
    name: str
    query: str = ""
    minus: str = ""
    filters: dict = {}


class SearchUpdate(BaseModel):
    name: str | None = None
    query: str | None = None
    minus: str | None = None
    filters: dict | None = None
    newCount: int | None = None


class ProductBody(BaseModel):
    id: str            # клиент генерит (prod_xxx) — как у поисков, для синхронного создания
    name: str
    ktru: list = []
    attributes: list = []


# ---------- вспомогательное ----------

def _row_to_user(row) -> dict:
    return {
        "id": row["id"], "name": row["name"], "email": row["email"], "role": row["role"],
        "totpEnabled": bool(row["totp_enabled"]),
        "telegramLinked": bool(row["telegram_chat_id"]),
    }


def _row_to_company(row) -> dict:
    expired = (not row["auto_renew"]) and datetime.fromisoformat(row["plan_expires_at"]) <= datetime.now(timezone.utc)
    return {
        "id": row["id"], "name": row["name"], "inn": row["inn"],
        "plan": row["plan"], "planExpiresAt": row["plan_expires_at"],
        "autoRenew": bool(row["auto_renew"]), "isExpired": expired,
        "groupId": row["group_id"],
    }


def _require_user(session_token: str | None):
    conn = db.get_conn()
    user = auth.get_user_by_token(conn, session_token)
    if not user:
        conn.close()
        raise HTTPException(status_code=401, detail="Не авторизован")
    return conn, user


def _require_active_user(session_token: str | None):
    """Как _require_user, но дополнительно требует непросроченный тариф/демо.

    НЕ использовать на /auth/*, /company, /employees, /invoices* — по этим
    роутам человек должен попасть даже с истёкшим доступом (увидеть кабинет,
    оплатить счёт). Гейт — только на роутах реального использования продукта
    (доска закупок, история сверок, сохранённые поиски, /api/match/spec)."""
    conn, user = _require_user(session_token)
    company = conn.execute(
        "SELECT plan_expires_at, auto_renew FROM companies WHERE id = ?", (user["company_id"],)
    ).fetchone()
    if not company["auto_renew"] and datetime.fromisoformat(company["plan_expires_at"]) <= datetime.now(timezone.utc):
        conn.close()
        raise HTTPException(
            status_code=402,
            detail=f"Доступ закончился. Оформите тариф в Telegram-боте: https://t.me/{BOT_USERNAME}",
        )
    return conn, user


def _norm_email(email) -> str:
    return str(email).strip().lower()


def _check_password(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Пароль должен быть не короче 8 символов")


def _plural_ru(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение по числу: 1 сотрудник, 2 сотрудника, 5 сотрудников."""
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    d = n % 10
    if d == 1:
        return one
    if 2 <= d <= 4:
        return few
    return many


def _plan_label(plan: str) -> str:
    return {
        "demo": "Демо-доступ (3 дня)",
        "start": "Тариф «Старт»", "business": "Тариф «Бизнес»", "corp": "Тариф «Корпоративный»",
    }.get(plan, plan)


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
        if not body.consentPdn:
            raise HTTPException(status_code=400, detail="Требуется согласие на обработку персональных данных")
        _check_password(body.password)
        inn = body.inn.strip()
        _check_inn_free(conn, inn)

        now = datetime.now(timezone.utc)
        # Тарифа при регистрации ещё нет — 'demo' с plan_expires_at=now (уже
        # "истёк") до тех пор, пока человек не привяжет Telegram и не получит
        # 3 дня демо через бота (см. app/support.py _handle_start). Реальный
        # доступ к продукту гейтит _require_active_user.
        cur = conn.execute(
            "INSERT INTO companies (name, inn, plan, plan_expires_at, created_at) VALUES (?, ?, 'demo', ?, ?)",
            (body.companyName.strip(), inn, now.isoformat(), now.isoformat()),
        )
        company_id = cur.lastrowid
        pw_hash = auth.hash_password(body.password)
        link_token = secrets.token_urlsafe(24)
        marketing_at = now.isoformat() if body.consentMarketing else None
        cur = conn.execute(
            "INSERT INTO users (company_id, email, password_hash, name, role, created_at, telegram_link_token, "
            "consent_pdn_at, consent_marketing, consent_marketing_at) "
            "VALUES (?, ?, ?, ?, 'owner', ?, ?, ?, ?, ?)",
            (company_id, email, pw_hash, body.name.strip(), now.isoformat(), link_token,
             now.isoformat(), 1 if body.consentMarketing else 0, marketing_at),
        )
        user_id = cur.lastrowid
        conn.commit()

        token = auth.create_session(conn, user_id)
        auth.set_session_cookie(response, token)
        audit.log("register_ok", ip=ratelimit.client_ip(request), email=email, user=user_id)

        user_row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        company_row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
        return {
            "user": _row_to_user(user_row), "company": _row_to_company(company_row),
            "telegramLinkUrl": f"https://t.me/{BOT_USERNAME}?start={link_token}",
        }
    finally:
        conn.close()


@router.get("/telegram-link")
def get_telegram_link(lekalo_session: str | None = Cookie(default=None)):
    """Показать кнопку привязки Telegram повторно — человек мог закрыть окно
    при регистрации, а исходный токен из ответа register() больше нигде не
    хранится. Переиспользует незакрытый токен, если он ещё не был потрачен."""
    conn, user = _require_user(lekalo_session)
    try:
        if user["telegram_chat_id"]:
            return {"linked": True}
        token = user["telegram_link_token"]
        if not token:
            token = secrets.token_urlsafe(24)
            conn.execute("UPDATE users SET telegram_link_token = ? WHERE id = ?", (token, user["id"]))
            conn.commit()
        return {"linked": False, "telegramLinkUrl": f"https://t.me/{BOT_USERNAME}?start={token}"}
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
            audit.log("login_fail", ip=ip, email=email)
            raise HTTPException(status_code=401, detail="Неверная почта или пароль")
        ratelimit.reset_login(ip, email)  # верный пароль обнуляет счётчик

        if user_row["totp_enabled"]:
            # пароль верный, но нужен код 2FA — сессию пока НЕ выдаём
            token = totp.create_pending(user_row["id"])
            audit.log("login_ok_pending_2fa", ip=ip, user=user_row["id"])
            return {"needsTotp": True, "tempToken": token}

        token = auth.create_session(conn, user_row["id"])
        auth.set_session_cookie(response, token)
        audit.log("login_ok", ip=ip, user=user_row["id"])

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
        if not user_row or not totp.verify_code(totp.reveal_secret(user_row["totp_secret"]), body.code):
            ratelimit.record_totp_failure(ip)
            totp.fail_pending(body.tempToken)  # N неверных кодов на токен → токен сгорает
            audit.log("2fa_fail", ip=ip, user=user_id)
            raise HTTPException(status_code=401, detail="Неверный код")
        ratelimit.reset_totp(ip)
        totp.consume_pending(body.tempToken)  # успех — токен больше не нужен

        token = auth.create_session(conn, user_row["id"])
        auth.set_session_cookie(response, token)
        audit.log("2fa_ok", ip=ip, user=user_row["id"])

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


@router.post("/auth/logout-all")
def logout_all(response: Response, lekalo_session: str | None = Cookie(default=None)):
    """Выйти на всех устройствах — гасим все сессии пользователя (полезно при
    подозрении, что cookie утёк)."""
    conn, user = _require_user(lekalo_session)
    try:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
        conn.commit()
    finally:
        conn.close()
    auth.clear_session_cookie(response)
    audit.log("logout_all", user=user["id"])
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
        # в БД кладём защищённую форму (шифр, если задан LK_TOTP_KEY); юзеру
        # отдаём сырой секрет/QR — он нужен для привязки приложения.
        conn.execute("UPDATE users SET totp_secret = ?, totp_enabled = 0 WHERE id = ?",
                     (totp.protect_secret(secret), user["id"]))
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
        if not totp.verify_code(totp.reveal_secret(user["totp_secret"]), body.code):
            raise HTTPException(status_code=400, detail="Неверный код — проверьте время на телефоне и попробуйте ещё раз")
        conn.execute("UPDATE users SET totp_enabled = 1 WHERE id = ?", (user["id"],))
        conn.commit()
        audit.log("2fa_enabled", user=user["id"])
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
        audit.log("2fa_disabled", user=user["id"])
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
        out["employeeLimit"] = _employee_limit(out["plan"])
        # компании той же группы (кроме себя) — для индикатора «часть большой компании»
        out["groupCompanies"] = []
        if company_row["group_id"] is not None:
            siblings = conn.execute(
                "SELECT id, name FROM companies WHERE group_id = ? AND id != ? ORDER BY id",
                (company_row["group_id"], company_row["id"]),
            ).fetchall()
            out["groupCompanies"] = [{"id": s["id"], "name": s["name"]} for s in siblings]
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
        company_row = conn.execute(
            "SELECT plan FROM companies WHERE id = ?", (user["company_id"],)
        ).fetchone()
        limit = _employee_limit(company_row["plan"])
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE company_id = ?", (user["company_id"],)
        ).fetchone()["n"]
        if count >= limit:
            raise HTTPException(
                status_code=403,
                detail=(f"На тарифе «{_plan_label(company_row['plan'])}» — до {limit} "
                        f"{_plural_ru(limit, 'сотрудника', 'сотрудников', 'сотрудников')}. "
                        "Для большего числа повысьте тариф или заведите вторую компанию."),
            )
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


# ---------- доска закупок (общая по компании: статус воронки + ответственный) ----------

@router.get("/saved")
def list_saved(lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_active_user(lekalo_session)
    try:
        rows = conn.execute(
            "SELECT sp.data, sp.status, sp.assignee_id, u.name AS assignee_name "
            "FROM saved_purchases sp LEFT JOIN users u ON u.id = sp.assignee_id "
            "WHERE sp.company_id = ? ORDER BY sp.created_at DESC",
            (user["company_id"],),
        ).fetchall()
        out = []
        for r in rows:
            p = json.loads(r["data"])
            p["boardStatus"] = r["status"] or BOARD_STATUSES[0]
            p["assigneeId"] = r["assignee_id"]
            p["assigneeName"] = r["assignee_name"]
            out.append(p)
        return out
    finally:
        conn.close()


@router.post("/saved")
def add_saved(body: SavedBody, lekalo_session: str | None = Cookie(default=None)):
    """Добавить закупку на общую доску компании (или обновить статус, если уже там).
    Снятие с доски — DELETE /saved/{purchase_id}."""
    conn, user = _require_active_user(lekalo_session)
    try:
        pid = str(body.purchase.get("id") or "")
        if not pid:
            raise HTTPException(status_code=400, detail="У закупки нет id")
        status = body.status if body.status in BOARD_STATUSES else BOARD_STATUSES[0]
        existing = conn.execute(
            "SELECT id FROM saved_purchases WHERE company_id = ? AND purchase_id = ?",
            (user["company_id"], pid),
        ).fetchone()
        if existing:
            if body.status in BOARD_STATUSES:
                conn.execute("UPDATE saved_purchases SET status = ? WHERE id = ?", (status, existing["id"]))
                conn.commit()
            return {"added": True, "boardStatus": status}
        conn.execute(
            "INSERT INTO saved_purchases (user_id, company_id, purchase_id, data, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user["id"], user["company_id"], pid, json.dumps(body.purchase, ensure_ascii=False),
             status, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        audit.log("saved_add", user=user["id"], company=user["company_id"], purchase=pid, status=status)
        return {"added": True, "boardStatus": status}
    finally:
        conn.close()


@router.patch("/saved/{purchase_id}")
def patch_saved(purchase_id: str, body: BoardPatch, lekalo_session: str | None = Cookie(default=None)):
    """Сменить статус и/или ответственного у карточки на доске компании."""
    conn, user = _require_active_user(lekalo_session)
    try:
        row = conn.execute(
            "SELECT id FROM saved_purchases WHERE company_id = ? AND purchase_id = ?",
            (user["company_id"], purchase_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Закупка не на доске")
        fields = body.model_fields_set
        if "status" in fields:
            if body.status not in BOARD_STATUSES:
                raise HTTPException(status_code=400, detail="Неизвестный статус")
            conn.execute("UPDATE saved_purchases SET status = ? WHERE id = ?", (body.status, row["id"]))
        if "assignee_id" in fields:
            aid = body.assignee_id
            if aid is not None:
                ok = conn.execute(
                    "SELECT 1 FROM users WHERE id = ? AND company_id = ?", (aid, user["company_id"])
                ).fetchone()
                if not ok:
                    raise HTTPException(status_code=400, detail="Ответственный не из вашей компании")
            conn.execute("UPDATE saved_purchases SET assignee_id = ? WHERE id = ?", (aid, row["id"]))
        conn.commit()
        audit.log("saved_patch", user=user["id"], company=user["company_id"], purchase=purchase_id,
                  status=(body.status if "status" in fields else None),
                  assignee=(body.assignee_id if "assignee_id" in fields else None))
        return {"ok": True}
    finally:
        conn.close()


@router.delete("/saved/{purchase_id}")
def delete_saved(purchase_id: str, lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_active_user(lekalo_session)
    try:
        conn.execute(
            "DELETE FROM saved_purchases WHERE company_id = ? AND purchase_id = ?",
            (user["company_id"], purchase_id),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ---------- история сверок ТЗ (личная, привязана к user_id) ----------

@router.get("/tz-checks")
def list_tz_checks(lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_active_user(lekalo_session)
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
    conn, user = _require_active_user(lekalo_session)
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


# ---------- сохранённые поиски (личные, привязаны к user_id) ----------

def _row_to_search(row) -> dict:
    try:
        filters = json.loads(row["filters"])
    except (TypeError, ValueError):
        filters = {}
    return {
        "id": row["id"], "name": row["name"], "query": row["query"], "minus": row["minus"],
        "filters": filters, "newCount": row["new_count"],
    }


@router.get("/searches")
def list_searches(lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_active_user(lekalo_session)
    try:
        rows = conn.execute(
            "SELECT * FROM searches WHERE user_id = ? ORDER BY created_at", (user["id"],)
        ).fetchall()
        return [_row_to_search(r) for r in rows]
    finally:
        conn.close()


@router.post("/searches")
def create_search(body: SearchCreate, lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_active_user(lekalo_session)
    try:
        # id генерит клиент → строка с таким id может уже принадлежать другому
        # юзеру. Без этой проверки INSERT OR REPLACE затёр бы чужую запись,
        # переписав её на себя (межтенантная запись). Заняли — 409.
        owner = conn.execute("SELECT user_id FROM searches WHERE id = ?", (body.id,)).fetchone()
        if owner and owner["user_id"] != user["id"]:
            raise HTTPException(status_code=409, detail="Идентификатор поиска занят")
        # Лимит поисков по тарифу — считаем только для НОВОГО поиска (id ещё не
        # мой): повторная отправка того же id — это апдейт, он лимит не тратит.
        if not owner:
            company_row = conn.execute(
                "SELECT plan FROM companies WHERE id = ?", (user["company_id"],)
            ).fetchone()
            limit = _search_limit(company_row["plan"])
            if limit is not None:
                count = conn.execute(
                    "SELECT COUNT(*) AS n FROM searches WHERE user_id = ?", (user["id"],)
                ).fetchone()["n"]
                if count >= limit:
                    raise HTTPException(
                        status_code=403,
                        detail=(f"На тарифе «{_plan_label(company_row['plan'])}» — до {limit} "
                                f"{_plural_ru(limit, 'сохранённого поиска', 'сохранённых поисков', 'сохранённых поисков')}. "
                                "Повысьте тариф, чтобы добавить больше."),
                    )
        conn.execute(
            "INSERT OR REPLACE INTO searches (id, user_id, name, query, minus, filters, new_count, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            (body.id, user["id"], body.name.strip(), body.query, body.minus,
             json.dumps(body.filters, ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM searches WHERE id = ?", (body.id,)).fetchone()
        return _row_to_search(row)
    finally:
        conn.close()


@router.patch("/searches/{search_id}")
def update_search(search_id: str, body: SearchUpdate, lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_active_user(lekalo_session)
    try:
        existing = conn.execute(
            "SELECT * FROM searches WHERE id = ? AND user_id = ?", (search_id, user["id"])
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Поиск не найден")
        fields, params = [], []
        if body.name is not None:
            fields.append("name = ?"); params.append(body.name.strip())
        if body.query is not None:
            fields.append("query = ?"); params.append(body.query)
        if body.minus is not None:
            fields.append("minus = ?"); params.append(body.minus)
        if body.filters is not None:
            fields.append("filters = ?"); params.append(json.dumps(body.filters, ensure_ascii=False))
        if body.newCount is not None:
            fields.append("new_count = ?"); params.append(body.newCount)
        if fields:
            params.append(search_id)
            conn.execute(f"UPDATE searches SET {', '.join(fields)} WHERE id = ?", params)
            conn.commit()
        row = conn.execute("SELECT * FROM searches WHERE id = ?", (search_id,)).fetchone()
        return _row_to_search(row)
    finally:
        conn.close()


@router.delete("/searches/{search_id}")
def delete_search(search_id: str, lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_active_user(lekalo_session)
    try:
        conn.execute("DELETE FROM searches WHERE id = ? AND user_id = ?", (search_id, user["id"]))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ---------- карточки товара (личные, привязаны к user_id; топливо движка matcher) ----------

def _row_to_product(row) -> dict:
    try:
        ktru = json.loads(row["ktru"])
    except (TypeError, ValueError):
        ktru = []
    try:
        attrs = json.loads(row["attributes"])
    except (TypeError, ValueError):
        attrs = []
    return {"id": row["id"], "name": row["name"], "ktru": ktru, "attributes": attrs}


@router.get("/products")
def list_products(lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_active_user(lekalo_session)
    try:
        rows = conn.execute(
            "SELECT * FROM products WHERE user_id = ? ORDER BY created_at", (user["id"],)
        ).fetchall()
        return [_row_to_product(r) for r in rows]
    finally:
        conn.close()


@router.post("/products")
def create_product(body: ProductBody, lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_active_user(lekalo_session)
    try:
        # id генерит клиент → строка с таким id может принадлежать другому юзеру.
        # Без проверки INSERT OR REPLACE затёр бы чужую запись (межтенантная запись).
        owner = conn.execute("SELECT user_id FROM products WHERE id = ?", (body.id,)).fetchone()
        if owner and owner["user_id"] != user["id"]:
            raise HTTPException(status_code=409, detail="Идентификатор товара занят")
        # Лимит по тарифу — только для НОВОГО товара (повторный тот же id — апдейт).
        if not owner:
            company_row = conn.execute(
                "SELECT plan FROM companies WHERE id = ?", (user["company_id"],)
            ).fetchone()
            limit = _product_limit(company_row["plan"])
            if limit is not None:
                count = conn.execute(
                    "SELECT COUNT(*) AS n FROM products WHERE user_id = ?", (user["id"],)
                ).fetchone()["n"]
                if count >= limit:
                    raise HTTPException(
                        status_code=403,
                        detail=(f"На тарифе «{_plan_label(company_row['plan'])}» — до {limit} "
                                f"{_plural_ru(limit, 'товара', 'товаров', 'товаров')} для сверки. "
                                "Повысьте тариф, чтобы добавить больше."),
                    )
        conn.execute(
            "INSERT OR REPLACE INTO products (id, user_id, name, ktru, attributes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (body.id, user["id"], body.name.strip(),
             json.dumps(body.ktru, ensure_ascii=False), json.dumps(body.attributes, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM products WHERE id = ?", (body.id,)).fetchone()
        return _row_to_product(row)
    finally:
        conn.close()


@router.delete("/products/{product_id}")
def delete_product(product_id: str, lekalo_session: str | None = Cookie(default=None)):
    conn, user = _require_active_user(lekalo_session)
    try:
        conn.execute("DELETE FROM products WHERE id = ? AND user_id = ?", (product_id, user["id"]))
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
def admin_page(request: Request, credentials: HTTPBasicCredentials = Depends(basic)):
    ip = ratelimit.client_ip(request)
    ratelimit.guard_admin(ip)  # HTTP Basic сам не лимитирует → троттлим перебор
    try:
        _check_admin(credentials)
    except HTTPException:
        ratelimit.record_admin_failure(ip)
        audit.log("admin_fail", ip=ip, user=credentials.username)
        raise
    ratelimit.reset_admin(ip)
    audit.log("admin_access", ip=ip)
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
            plan_extra = " · ♻️ автопродление" if c["auto_renew"] else ""
            cid = c["id"]
            actions = [
                f'<a href="/api/admin/companies/{cid}/revoke-auto-renew">Снять автопродление</a>' if c["auto_renew"]
                else f'<a href="/api/admin/companies/{cid}/grant-business">Выдать Бизнес без оплаты</a>',
                # аннулировать подписку — отрезать доступ сейчас (аккаунт остаётся)
                f'<a href="/api/admin/companies/{cid}/revoke-subscription" '
                f'onclick="return confirm(\'Аннулировать подписку компании #{cid}? Доступ отключится сразу, аккаунт останется.\')">'
                f'Аннулировать подписку</a>',
                # удалить компанию целиком — POST + подтверждение (необратимо)
                f'<form method="post" action="/api/admin/companies/{cid}/delete" style="display:inline" '
                f'onsubmit="return confirm(\'УДАЛИТЬ компанию #{cid} со всеми сотрудниками и данными? Это необратимо.\')">'
                f'<button type="submit" style="color:#b3401f;background:none;border:none;cursor:pointer;padding:0;font:inherit;text-decoration:underline">'
                f'Удалить компанию</button></form>',
            ]
            action = "<br>".join(actions)
            limit = _employee_limit(c["plan"])
            over = " style='color:#b3401f;font-weight:600'" if len(users) > limit else ""
            group_badge = (f'<br><span style="color:#b3401f;font-size:.8em">👥 группа #{c["group_id"]}</span>'
                           if c["group_id"] is not None else "")
            group_cell = (
                (f'<span style="color:#b3401f">#{c["group_id"]}</span> '
                 f'<a href="/api/admin/companies/{c["id"]}/unlink" style="font-size:.85em">убрать</a><br>'
                 if c["group_id"] is not None else '<span style="color:#aaa">—</span><br>')
                + f'<form method="get" action="/api/admin/companies/{c["id"]}/link" style="margin:4px 0 0;display:inline">'
                  f'<input name="with" placeholder="№" required style="width:52px;padding:2px 4px">'
                  f'<button type="submit" style="font-size:.85em">связать</button></form>'
            )
            rows_html.append(f"""
              <tr>
                <td>#{c['id']}</td>
                <td>{html.escape(c['name'])}{group_badge}<br><span style="color:#888;font-size:.85em">ИНН {html.escape(c['inn'] or '—')}</span></td>
                <td>{html.escape(owner['email']) if owner else '—'}</td>
                <td>{_plan_label(c['plan'])}{plan_extra}<br><span style="color:#888;font-size:.85em">до {c['plan_expires_at'][:10]}</span></td>
                <td{over}>{len(users)} / {limit}</td>
                <td><ul style="margin:0;padding-left:18px;">{users_html}</ul></td>
                <td style="font-size:.85em">{group_cell}</td>
                <td style="color:#888;font-size:.85em">{c['created_at'][:16].replace('T',' ')}</td>
                <td style="font-size:.85em">{action}</td>
              </tr>""")
        page_html = f"""
        <html><head><meta charset="utf-8"><title>Лекало — зарегистрированные компании</title>
        <style>
          body {{ font-family: -apple-system, Segoe UI, sans-serif; margin: 30px; color: #222; }}
          table {{ border-collapse: collapse; width: 100%; }}
          th, td {{ border: 1px solid #ddd; padding: 8px 10px; text-align: left; vertical-align: top; font-size: .92rem; }}
          th {{ background: #f4f1ea; }}
          h1 {{ font-size: 1.3rem; }}
          a {{ color: #b3401f; }}
        </style></head>
        <body>
          <h1>Зарегистрированные компании ({len(companies)}) · <a href="/api/admin/invoices">счета →</a></h1>
          <table>
            <tr><th>№</th><th>Компания</th><th>Email владельца</th><th>Тариф</th><th>Сотр. / лимит</th><th>Список</th><th>Группа</th><th>Регистрация</th><th>Действие</th></tr>
            {''.join(rows_html) or '<tr><td colspan="9">Пока никто не зарегистрировался</td></tr>'}
          </table>
        </body></html>"""
        return HTMLResponse(page_html)
    finally:
        conn.close()


def _guard_admin(request: Request, credentials: HTTPBasicCredentials) -> None:
    """Тот же бруфорс-щит/аудит, что у admin_page — переиспользуется
    grant-business/revoke-auto-renew (invoices.py дублирует то же самое под
    тем же именем для своих роутов, каждый модуль независим)."""
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


@router.get("/admin/companies/{company_id}/grant-business")
def admin_grant_business(company_id: int, request: Request, credentials: HTTPBasicCredentials = Depends(basic)):
    """Ручная привилегия владельца площадки: тариф «Бизнес» без счёта и без
    оплаты, с автопродлением (см. _require_active_user — auto_renew=1
    полностью выключает гейт по plan_expires_at). Для коллег/тестовых
    аккаунтов, не для обычных клиентов."""
    _guard_admin(request, credentials)
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT id FROM companies WHERE id = ?", (company_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Компания не найдена")
        expires = datetime.now(timezone.utc) + timedelta(days=365 * AUTO_RENEW_YEARS_AHEAD)
        conn.execute(
            "UPDATE companies SET plan='business', plan_expires_at=?, auto_renew=1 WHERE id=?",
            (expires.isoformat(), company_id),
        )
        conn.commit()
        audit.log("admin_grant_business", company=company_id)
        return RedirectResponse(url="/api/admin", status_code=303)
    finally:
        conn.close()


@router.get("/admin/companies/{company_id}/revoke-auto-renew")
def admin_revoke_auto_renew(company_id: int, request: Request, credentials: HTTPBasicCredentials = Depends(basic)):
    """Снять автопродление — компания возвращается к обычной схеме (тариф
    живёт до plan_expires_at, дальше — счёт/оплата как у всех). Саму дату
    не трогаем: если до неё ещё далеко, доступ не обрывается сразу."""
    _guard_admin(request, credentials)
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT id FROM companies WHERE id = ?", (company_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Компания не найдена")
        conn.execute("UPDATE companies SET auto_renew=0 WHERE id=?", (company_id,))
        conn.commit()
        audit.log("admin_revoke_auto_renew", company=company_id)
        return RedirectResponse(url="/api/admin", status_code=303)
    finally:
        conn.close()


@router.get("/admin/companies/{company_id}/revoke-subscription")
def admin_revoke_subscription(company_id: int, request: Request, credentials: HTTPBasicCredentials = Depends(basic)):
    """Аннулировать подписку — отрезать доступ к продукту СРАЗУ. В отличие от
    revoke-auto-renew (снимает автопродление, но доступ живёт до plan_expires_at),
    здесь ставим срок в прошлое и снимаем автопродление, поэтому
    _require_active_user начинает возвращать 402 немедленно. Аккаунт при этом
    НЕ удаляется: человек может войти в кабинет и оформить тариф заново."""
    _guard_admin(request, credentials)
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT id FROM companies WHERE id = ?", (company_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Компания не найдена")
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE companies SET auto_renew=0, plan='demo', plan_expires_at=? WHERE id=?",
            (now, company_id),
        )
        conn.commit()
        audit.log("admin_revoke_subscription", company=company_id)
        return RedirectResponse(url="/api/admin", status_code=303)
    finally:
        conn.close()


@router.post("/admin/companies/{company_id}/delete")
def admin_delete_company(company_id: int, request: Request, credentials: HTTPBasicCredentials = Depends(basic)):
    """Удалить компанию целиком — вместе со всеми её пользователями, сессиями,
    доской, историей сверок, сохранёнными поисками, счетами (каскад по внешним
    ключам, PRAGMA foreign_keys=ON в db.get_conn). Необратимо, поэтому только
    POST (форма с подтверждением в админке), не GET-ссылка: случайный переход/
    префетч не должен сносить клиента."""
    _guard_admin(request, credentials)
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT id, group_id FROM companies WHERE id = ?", (company_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Компания не найдена")
        group_id = row["group_id"]
        conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))
        # Группа из одного участника бессмысленна — если после удаления в группе
        # остался один, снимаем группу и с него (та же логика, что у unlink).
        if group_id is not None:
            rest = conn.execute("SELECT id FROM companies WHERE group_id = ?", (group_id,)).fetchall()
            if len(rest) == 1:
                conn.execute("UPDATE companies SET group_id = NULL WHERE id = ?", (rest[0]["id"],))
        conn.commit()
        audit.log("admin_delete_company", company=company_id)
        return RedirectResponse(url="/api/admin", status_code=303)
    finally:
        conn.close()


@router.get("/admin/companies/{company_id}/link")
def admin_link_companies(company_id: int, request: Request,
                         with_: int = Query(..., alias="with"),
                         credentials: HTTPBasicCredentials = Depends(basic)):
    """Связать две компании в одну «группу» (клиент с >10 сотрудников заводит
    вторую компанию — см. накопитель тарифов). group_id общий у всех компаний
    группы; сид группы — id первой из связываемых. Слияние двух уже существующих
    групп переводит вторую в первую."""
    _guard_admin(request, credentials)
    if company_id == with_:
        raise HTTPException(status_code=400, detail="Нельзя связать компанию саму с собой")
    conn = db.get_conn()
    try:
        a = conn.execute("SELECT id, group_id FROM companies WHERE id = ?", (company_id,)).fetchone()
        b = conn.execute("SELECT id, group_id FROM companies WHERE id = ?", (with_,)).fetchone()
        if not a or not b:
            raise HTTPException(status_code=404, detail="Одна из компаний не найдена")
        ga, gb = a["group_id"], b["group_id"]
        if ga is not None and gb is not None:
            if ga != gb:
                conn.execute("UPDATE companies SET group_id = ? WHERE group_id = ?", (ga, gb))
        elif ga is not None:
            conn.execute("UPDATE companies SET group_id = ? WHERE id = ?", (ga, with_))
        elif gb is not None:
            conn.execute("UPDATE companies SET group_id = ? WHERE id = ?", (gb, company_id))
        else:
            conn.execute("UPDATE companies SET group_id = ? WHERE id IN (?, ?)",
                         (company_id, company_id, with_))
        conn.commit()
        audit.log("admin_link_companies", company=company_id, group_with=with_)
        return RedirectResponse(url="/api/admin", status_code=303)
    finally:
        conn.close()


@router.get("/admin/companies/{company_id}/unlink")
def admin_unlink_company(company_id: int, request: Request,
                         credentials: HTTPBasicCredentials = Depends(basic)):
    """Убрать компанию из группы. Если после этого в группе остаётся одна
    компания — снимаем группу и с неё (группа из одного участника бессмысленна)."""
    _guard_admin(request, credentials)
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT group_id FROM companies WHERE id = ?", (company_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Компания не найдена")
        g = row["group_id"]
        if g is not None:
            conn.execute("UPDATE companies SET group_id = NULL WHERE id = ?", (company_id,))
            rest = conn.execute("SELECT id FROM companies WHERE group_id = ?", (g,)).fetchall()
            if len(rest) == 1:
                conn.execute("UPDATE companies SET group_id = NULL WHERE id = ?", (rest[0]["id"],))
            conn.commit()
            audit.log("admin_unlink_company", company=company_id, group=g)
        return RedirectResponse(url="/api/admin", status_code=303)
    finally:
        conn.close()
