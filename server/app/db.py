"""SQLite-хранилище аккаунтов: компании, пользователи, сессии, избранное, история сверок ТЗ.

Файл БД — server/data/lekalo.db (не коммитится, см. .gitignore: содержит хэши паролей).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DB_PATH = os.getenv("LK_DB_PATH", str(Path(__file__).resolve().parent.parent / "data" / "lekalo.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    inn TEXT,
    plan TEXT NOT NULL DEFAULT 'business',
    plan_expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'employee',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS saved_purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purchase_id TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, purchase_id)
);
CREATE TABLE IF NOT EXISTS tz_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purchase_id TEXT,
    purchase_number TEXT,
    purchase_title TEXT,
    score INTEGER,
    verdict TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    plan TEXT NOT NULL,
    amount INTEGER NOT NULL,
    vat INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'unpaid',
    created_at TEXT NOT NULL,
    paid_at TEXT
);
CREATE TABLE IF NOT EXISTS support_relay (
    admin_msg_id INTEGER PRIMARY KEY,
    client_chat_id TEXT NOT NULL,
    client_name TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS searches (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    query TEXT NOT NULL DEFAULT '',
    minus TEXT NOT NULL DEFAULT '',
    filters TEXT NOT NULL DEFAULT '{}',
    new_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
-- дедуп ежедневного дайджеста о новых подходящих закупках (см.
-- scripts/notify_new_purchases.py): одна строка = «этому пользователю про эту
-- закупку уже сообщали (или отметили при сиде первого прогона)».
CREATE TABLE IF NOT EXISTS notified_purchases (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purchase_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, purchase_id)
);
-- карточки товара для движка сверки (matcher). Личные у пользователя (как
-- история сверок), id генерит клиент (prod_xxx) — синхронное создание на фронте.
-- Раньше товары жили только в localStorage браузера и терялись при смене
-- устройства; теперь хранятся на сервере с лимитом по тарифу.
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    ktru TEXT NOT NULL DEFAULT '[]',
    attributes TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
-- Кэш LLM-извлечения требований (см. app/spec_llm.py): ktrutable.js не находит
-- структурированную таблицу характеристик примерно у 87% позиций 44-ФЗ и 98%
-- 223-ФЗ (замер 2026-08-28), а название позиции само часто несёт характеристики
-- текстом («Материал: фанера... Размеры: 75х40 см...»). Зовём LLM лениво, только
-- на позицию, для которой уже выбран товар пользователя (см. spec_match.match_lot),
-- поэтому объём естественно ограничен реальным трафиком, а не всем корпусом.
-- Ключ — хэш НАЗВАНИЯ позиции (не purchase_id): одинаковые описания товара
-- повторяются между закупками у одного каталога, кэш их не пересчитывает дважды.
-- ⚠️ Пишем в кэш ТОЛЬКО успешный вызов (пустой список requirements — тоже успех:
-- «спросили, LLM ничего не нашла»). Сетевая/API-ошибка НЕ кэшируется — иначе
-- разовый сбой канала навсегда запер бы позицию в «нечего проверять» (та же
-- логика, что у pending в tz-terms — см. корневой CLAUDE.md).
CREATE TABLE IF NOT EXISTS spec_llm_cache (
    name_hash TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    requirements TEXT NOT NULL,      -- JSON-массив, формат schema.Requirement
    created_at TEXT NOT NULL
);
-- Кэш LLM-добора align_keys (см. app/spec_match.py, matcher/src/keymatch.llm_map) —
-- Фаза 3 плана `piped-forging-flame`. Детерминированный слой (Фаза 1) сводит ключи
-- ТЗ↔карточки морфологически (регистр/ё-е/разделители); остаток — за деньги, LLM
-- сравнивает ВСЕ оставшиеся поля ТЗ разом со ВСЕМИ полями карточки (значение
-- участвует в разрешении неоднозначности, см. keymatch._SYSTEM), поэтому единица
-- кэша — не пара ключей, а весь запрос целиком: хэш от (отсортированных пар
-- ключ+значение остатка ТЗ) + (отсортированных пар ключ+значение карточки). Тот же
-- набор повторяется между закупками, где заказчики используют боилерплейт-ТЗ по
-- одной товарной категории на один и тот же товар пользователя.
-- ⚠️ Кэшируем ТОЛЬКО успешный вызов, включая честный пустой mapping — та же логика,
-- что у spec_llm_cache. Сетевая/API-ошибка не кэшируется.
CREATE TABLE IF NOT EXISTS align_keys_cache (
    request_hash TEXT PRIMARY KEY,
    mapping TEXT NOT NULL,            -- JSON {remaining_key: card_key}
    created_at TEXT NOT NULL
);
-- Кэш align_values (см. app/spec_match.py, matcher/src/keymatch.align_values) — Фаза 2
-- плана `piped-forging-flame`. В отличие от align_keys_cache единица кэша — ОТДЕЛЬНАЯ
-- пара (ключ, значение ТЗ, значение карточки), не весь запрос разом: keymatch._VAL_SYSTEM
-- сравнивает значения попарно, не разрешая конфликты между полями между собой (в отличие
-- от align_keys, где имя каждого поля оценивается с оглядкой на ВСЕ поля карточки сразу),
-- поэтому одна и та же пара «материал: металл / нержавеющая сталь» повторно всплывает в
-- разных закупках и у разных позиций одного каталога — кэш её сводит один раз.
-- ⚠️ Кэшируем ТОЛЬКО успешный вызов (включая честное satisfies=false). Сетевая/API-ошибка
-- не кэшируется — та же логика, что у align_keys_cache/spec_llm_cache.
CREATE TABLE IF NOT EXISTS align_values_cache (
    pair_hash TEXT PRIMARY KEY,
    satisfies INTEGER NOT NULL,       -- 0/1
    created_at TEXT NOT NULL
);
-- Кэш LLM-извлечения ПО ПОЛНОМУ ТЕКСТУ ТЗ (см. app/spec_llm.py extract_from_text_cached,
-- matcher/src/extractor.py) — Фаза 4 плана `piped-forging-flame`. В отличие от
-- spec_llm_cache (ключ — хэш НАЗВАНИЯ позиции, переиспользуется между закупками с
-- одинаковым описанием товара) здесь ключ ОБЯЗАН включать закупку: текст документа
-- разный даже у позиций с одинаковым названием в разных закупках, а одинаковое имя
-- 'name_hash' смешало бы результаты чужих документов.
-- ⚠️ Кэшируем только успешный вызов (в т.ч. честный пустой requirements). Сетевая/
-- API-ошибка не кэшируется — та же логика, что у spec_llm_cache.
CREATE TABLE IF NOT EXISTS spec_llm_fulltext_cache (
    request_hash TEXT PRIMARY KEY,    -- hash(purchase_id + "|" + name)
    requirements TEXT NOT NULL,       -- JSON-массив, формат schema.Requirement
    created_at TEXT NOT NULL
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# (таблица, [(колонка, DDL-тип-и-дефолт), ...]) — добавляются, если ещё нет.
# ALTER TABLE ADD COLUMN в SQLite не умеет "IF NOT EXISTS", поэтому проверяем
# через PRAGMA table_info сами. Нужно, т.к. в проде уже есть реальные компании —
# пересоздавать таблицу с нуля нельзя.
MIGRATIONS = [
    ("users", [
        ("totp_secret", "TEXT"),
        ("totp_enabled", "INTEGER NOT NULL DEFAULT 0"),
        # привязка Telegram — chat_id заполняется после /start<токен> в боте;
        # link_token — одноразовый токен deep-link'а, выдаётся при регистрации,
        # обнуляется после успешной привязки (см. app/support.py)
        ("telegram_chat_id", "TEXT"),
        ("telegram_link_token", "TEXT"),
        # согласия (152-ФЗ): дата согласия на обработку ПДн (обязательное при
        # регистрации) и отдельное необязательное согласие на маркетинг-рассылку
        # в Telegram. Храним факт+момент; отзыв маркетинга сбрасывает флаг в 0.
        ("consent_pdn_at", "TEXT"),
        ("consent_marketing", "INTEGER NOT NULL DEFAULT 0"),
        ("consent_marketing_at", "TEXT"),
        # ежедневный дайджест новых закупок (scripts/notify_new_purchases.py):
        # seeded_at — момент первого прохода (тогда бэклог помечается «показанным»
        # без отправки, чтобы не спамить); enabled — тумблер отписки (по умолчанию вкл).
        ("notify_seeded_at", "TEXT"),
        ("notify_enabled", "INTEGER NOT NULL DEFAULT 1"),
    ]),
    # избранное стало общей доской компании: статус воронки + ответственный +
    # привязка карточки к компании (а не только к добавившему пользователю).
    ("saved_purchases", [
        ("company_id", "INTEGER"),
        ("status", "TEXT NOT NULL DEFAULT 'Интересно'"),
        ("assignee_id", "INTEGER"),
    ]),
    ("companies", [
        # ручная привилегия владельца площадки: тариф без счетов/оплаты,
        # гейт доступа (_require_active_user) игнорирует plan_expires_at
        ("auto_renew", "INTEGER NOT NULL DEFAULT 0"),
        # дедуп уведомлений об истечении — хранят значение plan_expires_at,
        # на которое уже отправлено; при продлении дата меняется, и
        # уведомление на новый срок уходит снова без ручного сброса
        ("notice_sent_for", "TEXT"),
        ("expired_offer_sent_for", "TEXT"),
        # «группа компаний»: когда клиенту нужно >10 сотрудников, он заводит
        # вторую компанию, и владелец площадки ВРУЧНУЮ из админки связывает их —
        # у обеих проставляется общий group_id (см. accounts.py admin link/unlink).
        # NULL = компания сама по себе, не в группе.
        ("group_id", "INTEGER"),
    ]),
    ("invoices", [
        # доставка счёта из Telegram-бота без cookie-сессии (см. GET /invoices/{id}/bot)
        ("access_token", "TEXT"),
    ]),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def _migrate_saved_to_company(conn: sqlite3.Connection) -> None:
    """Доска избранного — общая по компании. Старым личным строкам проставляем
    company_id по их пользователю, схлопываем возможные дубли (одна карточка на
    закупку в компании — берём самую свежую) и вешаем уникальный индекс, чтобы
    два сотрудника не плодили две карточки одной закупки."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(saved_purchases)")}
    if "company_id" not in cols:
        return
    conn.execute(
        "UPDATE saved_purchases SET company_id = "
        "(SELECT company_id FROM users WHERE users.id = saved_purchases.user_id) "
        "WHERE company_id IS NULL"
    )
    conn.execute(
        "DELETE FROM saved_purchases WHERE company_id IS NOT NULL AND id NOT IN "
        "(SELECT MAX(id) FROM saved_purchases WHERE company_id IS NOT NULL "
        "GROUP BY company_id, purchase_id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_saved_company_purchase "
        "ON saved_purchases(company_id, purchase_id)"
    )


def init_db() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        _migrate_saved_to_company(conn)
        conn.commit()
    finally:
        conn.close()
