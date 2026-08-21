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


async def get_updates(offset: int | None, timeout: int = 25) -> list[dict]:
    """Long-polling — резервный (сейчас фактически основной, см.
    app/support.py poll_updates) канал доставки апдейтов: 2026-08-21
    обнаружено, что вебхук (POST /telegram/webhook) не получает трафик от
    Telegram вообще — ни одного запроса в логах nginx, при этом исходящие
    запросы С СЕРВЕРА в Telegram (этот же Bot API) отвечают штатно за доли
    секунды. Похоже на одностороннюю сетевую проблему у хостинга. getUpdates
    идёт по заведомо рабочему исходящему каналу."""
    if not BOT_TOKEN:
        return []
    params: dict = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        params["offset"] = offset
    async with httpx.AsyncClient(timeout=timeout + 10) as client:
        r = await client.get(_API.format(token=BOT_TOKEN, method="getUpdates"), params=params)
    try:
        data = r.json()
    except ValueError:
        return []
    return (data.get("result") or []) if data.get("ok") else []


async def delete_webhook() -> None:
    """getUpdates и вебхук несовместимы (Telegram отвечает 409 Conflict на
    getUpdates, пока вебхук зарегистрирован) — снимаем его перед стартом
    поллинга. Идемпотентно: если вебхука уже нет, Bot API просто отвечает ok."""
    if not BOT_TOKEN:
        return
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(_API.format(token=BOT_TOKEN, method="deleteWebhook"))


def tariffs_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "Старт — 15 000 ₽/мес", "callback_data": "buy:start"}],
            [{"text": "Бизнес — 35 000 ₽/мес", "callback_data": "buy:business"}],
            [{"text": "Корпоративный — обсудить", "callback_data": "buy:corp"}],
        ]
    }
