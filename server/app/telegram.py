"""Общая точка отправки в Telegram Bot API — используется поддержкой
(app/support.py), выдачей демо/тарифов и cron-уведомлениями
(scripts/notify_expiring.py).

Раньше отправка жила приватной функцией внутри support.py — с ростом числа
мест, которые шлют сообщения клиенту (не только пересылка от владельца),
общий код вынесен сюда, чтобы токен и HTTP-обвязка не дублировались."""

from __future__ import annotations

import os

import httpx

BOT_TOKEN = os.getenv("LK_TELEGRAM_BOT_TOKEN", "")
BOT_USERNAME = os.getenv("LK_TELEGRAM_BOT_USERNAME", "Bot_Lekalo_bot")

_API = "https://api.telegram.org/bot{token}/{method}"


async def send_message(chat_id: str, text: str, reply_markup: dict | None = None) -> dict | None:
    if not BOT_TOKEN:
        return None
    payload: dict = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(_API.format(token=BOT_TOKEN, method="sendMessage"), json=payload)
    try:
        data = r.json()
    except ValueError:
        return None
    return data.get("result") if data.get("ok") else None


async def answer_callback_query(callback_query_id: str, text: str | None = None) -> None:
    """Обязательна после каждого callback_query — иначе кнопка в клиенте
    Telegram виснет с крутящимся индикатором до таймаута."""
    if not BOT_TOKEN:
        return
    payload: dict = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(_API.format(token=BOT_TOKEN, method="answerCallbackQuery"), json=payload)


def tariffs_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "Старт — 15 000 ₽/мес", "callback_data": "buy:start"}],
            [{"text": "Бизнес — 35 000 ₽/мес", "callback_data": "buy:business"}],
            [{"text": "Корпоративный — обсудить", "callback_data": "buy:corp"}],
        ]
    }
