"""Уведомления в Telegram об истечении демо/тарифа — cron, раз в час.

Запуск: cd server && .venv/bin/python scripts/notify_expiring.py

Демо предупреждаем за 24 часа до конца, платный тариф — за 7 дней. Плюс
отдельно: если демо УЖЕ истекло — предложение выбрать тариф (кнопки, тот же
код, что и команда /tariffs в боте). Платные тарифы после истечения повторно
не дёргаем — их продление идёт через выставленный счёт (см. app/invoices.py),
не через этот скрипт.

Дедуп — через companies.notice_sent_for/expired_offer_sent_for: хранят
значение plan_expires_at, на которое уже отправлено. При продлении (оплата
или ручной грант) дата меняется, и уведомление на новый срок уйдёт снова без
ручного сброса флага.

Компании с auto_renew=1 (ручная привилегия, см. accounts.py
admin_grant_business) пропускаются целиком — для них plan_expires_at не
значит "скоро кончится", гейт доступа (_require_active_user) его тоже
игнорирует.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
_SERVER = os.path.dirname(_SCRIPTS)
sys.path.insert(0, _SERVER)  # чтобы импортировался пакет app/

from app import db  # noqa: E402
from app.accounts import _plan_label  # noqa: E402
from app.telegram import send_message, tariffs_keyboard  # noqa: E402

DEMO_LEAD = timedelta(hours=24)
PAID_LEAD = timedelta(days=7)


async def main() -> None:
    conn = db.get_conn()
    try:
        now = datetime.now(timezone.utc)
        companies = conn.execute("SELECT * FROM companies WHERE auto_renew = 0").fetchall()
        sent_notice = sent_offer = 0
        for c in companies:
            owner = conn.execute(
                "SELECT telegram_chat_id FROM users WHERE company_id = ? AND role = 'owner'",
                (c["id"],),
            ).fetchone()
            chat_id = owner["telegram_chat_id"] if owner else None
            if not chat_id:
                continue  # Telegram ещё не привязан — уведомлять некуда

            expires = datetime.fromisoformat(c["plan_expires_at"])
            lead = DEMO_LEAD if c["plan"] == "demo" else PAID_LEAD
            if now <= expires <= now + lead and c["notice_sent_for"] != c["plan_expires_at"]:
                left = "меньше суток" if c["plan"] == "demo" else f"{(expires - now).days} дн."
                await send_message(
                    chat_id,
                    f"⏰ {_plan_label(c['plan'])} истекает {expires.date().isoformat()} "
                    f"(осталось {left}). Продлить — команда /tariffs.",
                )
                conn.execute("UPDATE companies SET notice_sent_for = ? WHERE id = ?", (c["plan_expires_at"], c["id"]))
                conn.commit()
                sent_notice += 1

            if c["plan"] == "demo" and expires <= now and c["expired_offer_sent_for"] != c["plan_expires_at"]:
                await send_message(chat_id, "Демо-доступ закончился. Чтобы продолжить пользоваться Лекало, выберите тариф:")
                await send_message(chat_id, "Тарифы:", reply_markup=tariffs_keyboard())
                conn.execute(
                    "UPDATE companies SET expired_offer_sent_for = ? WHERE id = ?", (c["plan_expires_at"], c["id"])
                )
                conn.commit()
                sent_offer += 1

        print(f"notify_expiring: проверено {len(companies)} компаний, "
              f"предупреждений {sent_notice}, офферов после демо {sent_offer}")
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
