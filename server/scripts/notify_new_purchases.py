"""Ежедневный дайджест в Telegram: новые подходящие закупки по сохранённым
поискам клиента.

Запуск (cron на VPS, раз в сутки — см. deploy/DEPLOY.md):
    cd server && .venv/bin/python scripts/notify_new_purchases.py

Логика:
  • читаем снапшот покупок (LK_SNAPSHOT_PATH, тот же файл, что отдаёт фронт);
  • для каждого пользователя с привязанным Telegram, включёнными уведомлениями
    и активным доступом берём его сохранённые поиски;
  • среди «живых» (ещё можно податься) закупок находим совпавшие по поиску —
    матчинг зеркалит ленту (app/search_match.py);
  • новизна — через таблицу notified_purchases: ПЕРВЫЙ проход для пользователя
    только помечает текущие совпадения показанными (сид), НЕ шлёт — иначе первый
    же дайджест был бы простынёй из тысяч старых закупок. Дальше уведомляем лишь
    о том, чего в таблице ещё нет.

Дедуп внутри прогона: закупка, попавшая под несколько поисков, уходит в дайджест
один раз — под первым совпавшим поиском.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
_SERVER = os.path.dirname(_SCRIPTS)
_REPO = os.path.dirname(_SERVER)
sys.path.insert(0, _SERVER)  # чтобы импортировался пакет app/

from app import db, search_match  # noqa: E402
from app.telegram import send_message  # noqa: E402

SNAPSHOT_PATH = os.getenv("LK_SNAPSHOT_PATH", os.path.join(_REPO, "site", "data", "purchases.json"))
PUBLIC_BASE_URL = os.getenv("LK_PUBLIC_BASE_URL", "https://185.11.134.80.nip.io")
TOP_PER_SEARCH = 5       # сколько закупок показывать под одним поиском (остальные — «…и ещё N»)
MAX_ITEMS_IN_MSG = 25    # общий предел строк-закупок в одном сообщении (лимит Telegram 4096)
PRUNE_DAYS = 90          # старше — вычищаем из notified_purchases, чтобы таблица не росла


def _plural(n, one, few, many):
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    d = n % 10
    if d == 1:
        return one
    if 2 <= d <= 4:
        return few
    return many


def _money(n):
    try:
        return f"{int(n):,}".replace(",", " ") + " ₽"
    except (TypeError, ValueError):
        return "цена не указана"


def _deadline(p):
    ed = p.get("endDate")
    if not ed:
        return ""
    dt = search_match._parse_dt(ed)
    return f", до {dt.strftime('%d.%m')}" if dt else ""


def _href(p):
    # та же подмена битой печатной модалки ЕИС, что platformHref на фронте
    h = str(p.get("href") or "")
    return h.replace("printForm/listModal.html", "printForm/view.html")


def _clip(s, n):
    s = str(s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _load_snapshot():
    with open(SNAPSHOT_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("purchases") or []


def _build_message(new_by_search):
    lines = ["🔔 Лекало — новые закупки по вашим сохранённым поискам:", ""]
    shown = 0
    for name, plist in new_by_search:
        n = len(plist)
        lines.append(f"📌 «{name}» — {n} {_plural(n, 'новая', 'новых', 'новых')}:")
        for p in plist[:TOP_PER_SEARCH]:
            if shown >= MAX_ITEMS_IN_MSG:
                break
            lines.append(f"• № {p.get('number', '')} {_clip(p.get('title'), 70)}")
            lines.append(f"  {_money(p.get('price'))}{_deadline(p)}\n  {_href(p)}")
            shown += 1
        if n > TOP_PER_SEARCH:
            lines.append(f"  …и ещё {n - TOP_PER_SEARCH}")
        lines.append("")
        if shown >= MAX_ITEMS_IN_MSG:
            lines.append("… и другие — смотрите в ленте.")
            break
    lines.append(f"Открыть ленту: {PUBLIC_BASE_URL}/app.html")
    return "\n".join(lines)


async def main():
    db.init_db()  # идемпотентно — гарантирует таблицу notified_purchases и колонки notify_*
    try:
        purchases = _load_snapshot()
    except (OSError, ValueError) as e:
        print(f"снапшот недоступен ({SNAPSHOT_PATH}): {e}", file=sys.stderr)
        return
    now = datetime.now(timezone.utc)
    # «живые» закупки считаем один раз — база для всех поисков всех пользователей
    actionable = [p for p in purchases if search_match.is_actionable(p, now)]

    conn = db.get_conn()
    try:
        now_iso = now.isoformat()
        users = conn.execute(
            "SELECT u.id AS id, u.telegram_chat_id AS chat_id, u.notify_seeded_at AS seeded "
            "FROM users u JOIN companies c ON c.id = u.company_id "
            "WHERE u.telegram_chat_id IS NOT NULL AND u.notify_enabled = 1 "
            "  AND (c.auto_renew = 1 OR c.plan_expires_at > ?)",
            (now_iso,),
        ).fetchall()

        sent = seeded = 0
        for u in users:
            searches = conn.execute(
                "SELECT name, query, minus, filters FROM searches WHERE user_id = ? ORDER BY created_at",
                (u["id"],),
            ).fetchall()
            if not searches:
                continue

            notified = {
                r["purchase_id"]
                for r in conn.execute(
                    "SELECT purchase_id FROM notified_purchases WHERE user_id = ?", (u["id"],)
                )
            }
            assigned = set()          # чтобы одна закупка не попала под два поиска в этом прогоне
            new_by_search = []
            for s in searches:
                try:
                    filters = json.loads(s["filters"]) if s["filters"] else {}
                except (TypeError, ValueError):
                    filters = {}
                sd = {"query": s["query"], "minus": s["minus"], "filters": filters}
                fresh = []
                for p in actionable:
                    pid = str(p.get("id") or "")
                    if not pid or pid in notified or pid in assigned:
                        continue
                    if search_match.purchase_matches(p, sd, now):
                        assigned.add(pid)
                        fresh.append(p)
                if fresh:
                    new_by_search.append((s["name"], fresh))

            if not assigned:
                # даже если совпадений нет, первый проход считаем сидом (нечего слать)
                if u["seeded"] is None:
                    conn.execute("UPDATE users SET notify_seeded_at = ? WHERE id = ?", (now_iso, u["id"]))
                    conn.commit()
                continue

            # запоминаем все назначенные как «показанные», чтобы не повторять
            conn.executemany(
                "INSERT OR IGNORE INTO notified_purchases (user_id, purchase_id, created_at) VALUES (?, ?, ?)",
                [(u["id"], str(p.get("id")), now_iso) for _, plist in new_by_search for p in plist],
            )

            if u["seeded"] is None:
                # первый проход — только сид, без рассылки бэклога
                conn.execute("UPDATE users SET notify_seeded_at = ? WHERE id = ?", (now_iso, u["id"]))
                conn.commit()
                seeded += 1
                continue

            conn.commit()
            text = _build_message(new_by_search)
            result = await send_message(u["chat_id"], text)
            if result:
                sent += 1

        # чистим давно уведомлённые записи, чтобы таблица не пухла
        cutoff = (now - timedelta(days=PRUNE_DAYS)).isoformat()
        conn.execute("DELETE FROM notified_purchases WHERE created_at < ?", (cutoff,))
        conn.commit()
        print(f"дайджест: отправлено {sent}, засеяно {seeded}, пользователей {len(users)}")
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
