"""Поддержка через Telegram-бота — двусторонняя пересылка без своей БД переписки.

Клиент пишет боту → сообщение уходит владельцу (LK_SUPPORT_ADMIN_CHAT_ID) с
припиской, от кого. Владелец отвечает в Telegram через "Ответить" (reply) на
это самое сообщение — вебхук находит по reply_to_message.message_id, какому
клиенту это адресовано (таблица support_relay — только сама пересылка, не
всю переписку хранить незачем), и пересылает ответ туда же.

Вебхук защищён secret_token: Telegram сам присылает его заголовком
X-Telegram-Bot-Api-Secret-Token, если он задан при setWebhook (см.
server/deploy/setup-telegram-bot.py) — без этого кто угодно мог бы слать
поддельные апдейты на открытый POST-эндпоинт.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Header, HTTPException, Request

from app import db

router = APIRouter(prefix="/api")

BOT_TOKEN = os.getenv("LK_TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = os.getenv("LK_SUPPORT_ADMIN_CHAT_ID", "")
WEBHOOK_SECRET = os.getenv("LK_TELEGRAM_WEBHOOK_SECRET", "")

_API = "https://api.telegram.org/bot{token}/{method}"


async def _send(chat_id: str, text: str) -> dict | None:
    if not BOT_TOKEN:
        return None
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(
            _API.format(token=BOT_TOKEN, method="sendMessage"),
            json={"chat_id": chat_id, "text": text},
        )
    try:
        data = r.json()
    except ValueError:
        return None
    return data.get("result") if data.get("ok") else None


def _who(frm: dict) -> str:
    if frm.get("username"):
        return f"@{frm['username']}"
    name = " ".join(p for p in (frm.get("first_name"), frm.get("last_name")) if p)
    return name or "клиент"


@router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="bad secret")
    if not BOT_TOKEN or not ADMIN_CHAT_ID:
        # Бот не настроен на этом окружении — тихо принимаем и ничего не делаем,
        # чтобы Telegram не долбил повторными доставками несуществующего апдейта.
        return {"ok": True}

    update = await request.json()
    msg = update.get("message") or update.get("edited_message")
    if not msg or "text" not in msg:
        return {"ok": True}

    chat_id = str(msg["chat"]["id"])
    text = msg["text"]

    # Владелец пишет боту — либо ответ клиенту (reply на пересланное), либо
    # что-то ещё (игнорируем, чтобы не зациклиться самому на себя).
    if chat_id == str(ADMIN_CHAT_ID):
        reply_to = msg.get("reply_to_message")
        if not reply_to:
            return {"ok": True}
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
        return {"ok": True}

    # Клиент пишет боту — пересылаем владельцу и запоминаем, кому отвечать.
    who = _who(msg.get("from") or {})
    sent = await _send(ADMIN_CHAT_ID, f"Сообщение от {who} (chat {chat_id}):\n{text}")
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
    await _send(chat_id, "Спасибо! Сообщение передано в поддержку, ответим здесь же.")
    return {"ok": True}
