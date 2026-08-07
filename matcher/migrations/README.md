# Миграции схемы БД (Alembic)

Версионирование схемы, чтобы редеплой не терял данные (за сессию 2026-07-29 в `Lead` накопилось
+6 колонок — без миграций персистентная БД ломается на редеплое).

URL берётся из `settings.database_url` (env `SPECMATCH_DATABASE_URL`) — в `alembic.ini` не хардкодим.
`render_as_batch=True` — чтобы ALTER работал на SQLite.

## Команды

```bash
# применить все миграции (prod-деплой: перед стартом приложения)
.venv/bin/alembic upgrade head

# создать миграцию после изменения моделей (api/models.py)
.venv/bin/alembic revision --autogenerate -m "что изменилось"
#   → просмотреть сгенерированный файл в migrations/versions/ ПЕРЕД применением!

# откатить на одну ревизию
.venv/bin/alembic downgrade -1

# текущая ревизия БД
.venv/bin/alembic current
```

## Прод vs dev/тесты

- **Prod:** схему держит ТОЛЬКО alembic (`upgrade head` до старта). `init_db()`/`create_all` не звать.
- **Dev/тесты:** `api.db.init_db()` (`create_all`) — быстро, без миграций. Начальная миграция
  автогенерирована из тех же моделей, так что схемы совпадают.
- **Переход существующей dev-БД на alembic:** если БД создана через `create_all` ДО alembic и уже
  содержит актуальные колонки — `alembic stamp head` (пометить как на базовой ревизии). Если колонок
  не хватает (старая схема) — пересоздать БД (`rm data/app.db && alembic upgrade head`), dev-данные
  одноразовы.
