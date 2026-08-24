"""Поддержка через Telegram-бота — двусторонняя пересылка без своей БД переписки.

Клиент пишет боту → сообщение уходит владельцу (LK_SUPPORT_ADMIN_CHAT_ID) с
припиской, от кого. Владелец отвечает в Telegram через "Ответить" (reply) на
это самое сообщение — вебхук находит по reply_to_message.message_id, какому
клиенту это адресовано (таблица support_relay — только сама пересылка, не
всю переписку хранить незачем), и пересылает ответ туда же.

⚠️ Доставка апдейтов — long-polling (poll_updates, запущен из main.py
lifespan), НЕ вебхук. Обнаружено 2026-08-21: POST /telegram/webhook не
получал от Telegram ни одного запроса (пусто в логах nginx), хотя исходящие
запросы с сервера в Telegram отвечали штатно — похоже на одностороннюю
сетевую проблему у хостинга, не связанную с кодом или конфигом nginx (ufw
неактивен, iptables пустой, порт 443 слушается, сертификат валиден).
Роут /telegram/webhook оставлен нетронутым на случай, если сетевая проблема
разрешится — секрет всё ещё проверяется, вреда от простаивающего роута нет.
Обработка апдейта — process_update(), общая для обоих каналов.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException, Request

from app import audit, db, invoices
from app.accounts import DEMO_DAYS
from app.telegram import answer_callback_query, delete_webhook, get_updates, tariffs_keyboard
from app.telegram import send_message as _send

_log = logging.getLogger("lekalo.telegram")

router = APIRouter(prefix="/api")

BOT_TOKEN = os.getenv("LK_TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = os.getenv("LK_SUPPORT_ADMIN_CHAT_ID", "")
WEBHOOK_SECRET = os.getenv("LK_TELEGRAM_WEBHOOK_SECRET", "")
# для ссылок на счета, которые бот шлёт клиенту (см. _handle_callback) —
# тот же домен, что в LK_CORS_ORIGINS по умолчанию (main.py)
PUBLIC_BASE_URL = os.getenv("LK_PUBLIC_BASE_URL", "https://147.45.141.237.nip.io")
# смещение getUpdates переживает рестарт процесса (иначе после каждого
# деплоя Telegram присылал бы заново весь недавний бэклог апдейтов)
_OFFSET_FILE = os.path.join(os.path.dirname(db.DB_PATH), "telegram_offset.txt")

ABOUT_TEXT = """🎯 Лекало — все торги России в одной ленте, с умной сверкой по ТЗ

Ищем закупки по 44-ФЗ и 223-ФЗ на двух площадках — ЕИС (zakupki.gov.ru) и \
Портал поставщиков (mos.ru) — как в Тендерплане и Контуре.

А сверху — то, чего нет больше нигде: Лекало читает техническое задание \
каждой закупки и сверяет его с характеристиками вашего товара. На выходе — \
процент совпадения и разбор по каждому требованию, а не просто совпадение \
по коду ОКПД2.

Как получить доступ:
1. Регистрация на сайте (2 минуты)
2. Привязка Telegram — вы это уже сделали 🙂
3. 3 дня демо-доступа бесплатно
4. Дальше — тариф: команда /tariffs

Тарифы:
• Старт — 15 000 ₽/мес, 1 товар для сверки
• Бизнес — 35 000 ₽/мес, до 10 товаров, сборные лоты, Telegram-уведомления
• Корпоративный — по запросу, безлимит + API + персональный менеджер

Вопросы — просто напишите сюда, ответим."""


def _who(frm: dict) -> str:
    if frm.get("username"):
        return f"@{frm['username']}"
    name = " ".join(p for p in (frm.get("first_name"), frm.get("last_name")) if p)
    return name or "клиент"


async def _relay_to_admin(chat_id: str, who: str, text: str) -> None:
    """Переслать сообщение клиента владельцу и запомнить, кому адресован
    будущий reply (support_relay) — общая часть для обычной переписки и для
    заявки на тариф «Корпоративный» (см. _handle_callback)."""
    sent = await _send(ADMIN_CHAT_ID, text)
    if sent and sent.get("message_id"):
        conn = db.get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO support_relay (admin_msg_id, client_chat_id, client_name, created_at) "
                "VALUES (?, ?, ?, ?)",
                (sent["message_id"], chat_id, who, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()


async def _handle_start(chat_id: str, token: str) -> None:
    """/start <token> — deep-link из кабинета (register()/GET /telegram-link
    в accounts.py). Привязывает chat_id к аккаунту и, если демо ещё ни разу
    не выдавалось, включает 3 дня демо-доступа."""
    conn = db.get_conn()
    try:
        user = conn.execute("SELECT * FROM users WHERE telegram_link_token = ?", (token,)).fetchone()
        if not user:
            already = conn.execute(
                "SELECT 1 FROM users WHERE telegram_chat_id = ?", (chat_id,)
            ).fetchone()
            await _send(
                chat_id,
                "Telegram уже подключён к аккаунту." if already
                else "Ссылка недействительна или уже использована — зайдите в кабинет и запросите новую.",
            )
            return
        conn.execute(
            "UPDATE users SET telegram_chat_id = ?, telegram_link_token = NULL WHERE id = ?",
            (chat_id, user["id"]),
        )
        company = conn.execute("SELECT * FROM companies WHERE id = ?", (user["company_id"],)).fetchone()
        activated_until = None
        # демо выдаём один раз: если план уже продлевали (оплата/грант), не трогаем
        if company["plan"] == "demo" and datetime.fromisoformat(company["plan_expires_at"]) <= datetime.now(timezone.utc):
            activated_until = datetime.now(timezone.utc) + timedelta(days=DEMO_DAYS)
            conn.execute(
                "UPDATE companies SET plan_expires_at = ? WHERE id = ?",
                (activated_until.isoformat(), company["id"]),
            )
        conn.commit()
        audit.log("telegram_linked", user=user["id"], company=user["company_id"])
    finally:
        conn.close()
    await _send(chat_id, ABOUT_TEXT)
    if activated_until:
        await _send(chat_id, f"✅ Демо-доступ активирован, действует до {activated_until.date().isoformat()}.")


async def _send_tariffs_menu(chat_id: str) -> None:
    await _send(chat_id, "Выберите тариф:", reply_markup=tariffs_keyboard())


async def _handle_callback(cq: dict) -> None:
    chat_id = str(cq["from"]["id"])
    data = cq.get("data") or ""
    cq_id = cq["id"]
    if not data.startswith("buy:"):
        await answer_callback_query(cq_id)
        return
    plan = data.split(":", 1)[1]

    if plan == "corp":
        await answer_callback_query(cq_id, "Передали в поддержку")
        who = _who(cq.get("from") or {})
        await _relay_to_admin(chat_id, who, f"{who} (chat {chat_id}) интересуется тарифом «Корпоративный»")
        await _send(chat_id, "Передали запрос по тарифу «Корпоративный» — скоро ответим здесь же.")
        return

    conn = db.get_conn()
    try:
        user = conn.execute("SELECT * FROM users WHERE telegram_chat_id = ?", (chat_id,)).fetchone()
        if not user:
            await answer_callback_query(cq_id)
            await _send(chat_id, "Сначала привяжите аккаунт — кнопка «Подключить Telegram» в личном кабинете.")
            return
        try:
            result = invoices.create_invoice_for_company(conn, user["company_id"], plan)
        except HTTPException:
            await answer_callback_query(cq_id)
            await _send(chat_id, "Этот тариф выставляется по запросу — напишите сюда, обсудим.")
            return
    finally:
        conn.close()

    await answer_callback_query(cq_id, "Счёт выставлен")
    link = f"{PUBLIC_BASE_URL}/api/invoices/{result['id']}/bot?t={result['accessToken']}"
    await _send(
        chat_id,
        f"Счёт № {result['number']} выставлен: {link}\n\n"
        "Оплата — банковским переводом по указанным в счёте реквизитам. После "
        "поступления оплаты подтвердим вручную — тариф активируется, и мы "
        "пришлём подтверждение сюда же.",
    )


async def process_update(update: dict) -> None:
    """Разбор одного апдейта Telegram — общий код для вебхука (сейчас не
    получает трафика, см. докстринг модуля) и long-polling (poll_updates,
    фактический канал доставки)."""
    if not BOT_TOKEN or not ADMIN_CHAT_ID:
        return  # бот не настроен на этом окружении

    if "callback_query" in update:
        await _handle_callback(update["callback_query"])
        return

    msg = update.get("message") or update.get("edited_message")
    if not msg or "text" not in msg:
        return

    chat_id = str(msg["chat"]["id"])
    text = msg["text"].strip()

    # Команды — раньше проверки "это владелец?" ниже. Владелец — тоже
    # обычный аккаунт (например, свой же тестовый), и /start от него не
    # должен молча проглатываться веткой "ответ клиенту".
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        if len(parts) == 2:
            await _handle_start(chat_id, parts[1].strip())
        else:
            await _send(chat_id, ABOUT_TEXT)
        return
    if text.lower() in ("/tariffs", "/тарифы"):
        await _send_tariffs_menu(chat_id)
        return
    if text.lower() in ("/about", "/помощь", "/help"):
        await _send(chat_id, ABOUT_TEXT)
        return

    # Владелец пишет боту не команду — либо ответ клиенту (reply на
    # пересланное), либо что-то ещё (игнорируем, чтобы не зациклиться самому
    # на себя пересылкой в поддержку).
    if chat_id == str(ADMIN_CHAT_ID):
        reply_to = msg.get("reply_to_message")
        if not reply_to:
            return
        conn = db.get_conn()
        try:
            row = conn.execute(
                "SELECT client_chat_id FROM support_relay WHERE admin_msg_id = ?",
                (reply_to["message_id"],),
            ).fetchone()
        finally:
            conn.close()
        if row:
            await _send(row["client_chat_id"], text)
        return

    # Клиент пишет боту что-то ещё — пересылаем владельцу и запоминаем, кому отвечать.
    who = _who(msg.get("from") or {})
    await _relay_to_admin(chat_id, who, f"Сообщение от {who} (chat {chat_id}):\n{text}")
    await _send(chat_id, "Спасибо! Сообщение передано в поддержку, ответим здесь же.")


@router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="bad secret")
    update = await request.json()
    await process_update(update)
    return {"ok": True}


def _load_offset() -> int | None:
    try:
        with open(_OFFSET_FILE, encoding="utf-8") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def _save_offset(offset: int) -> None:
    with open(_OFFSET_FILE, "w", encoding="utf-8") as f:
        f.write(str(offset))


async def poll_updates() -> None:
    """Фоновая задача (запускается из main.py lifespan) — держит соединение
    getUpdates открытым, снимает вебхук перед стартом (несовместимы, см.
    telegram.delete_webhook). Сетевые сбои (обрыв соединения и т.п.) не
    останавливают цикл — короткая пауза и следующая попытка; ошибка внутри
    обработки ОДНОГО апдейта не должна ронять весь цикл и терять offset."""
    if not BOT_TOKEN or not ADMIN_CHAT_ID:
        return
    await delete_webhook()
    offset = _load_offset()
    while True:
        try:
            updates = await get_updates(offset, timeout=25)
        except Exception:
            _log.exception("getUpdates упал")
            await asyncio.sleep(5)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            try:
                await process_update(u)
            except Exception:
                _log.exception("process_update упал на апдейте %s", u.get("update_id"))
            _save_offset(offset)
