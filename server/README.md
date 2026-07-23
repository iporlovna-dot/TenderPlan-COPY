# Бэкенд — Лекало (поиск торгов)

FastAPI-сервис: агрегирует закупки с площадок за единым интерфейсом источника,
отдаёт ленту с фильтрами, карточки, документы и сверку по ТЗ. Первая площадка —
**Портал поставщиков** (zakupki.mos.ru), публичные анонимные эндпоинты.

## Структура

```
server/
  app/
    schema.py          модели Purchase/Lot/Document/MatchResult (форма = как во фронте)
    matching.py        парсинг ТЗ (docx/pdf/xlsx) + детерминированная сверка
    main.py            FastAPI: /api/purchases, /api/purchases/{id}, /api/documents/{id}, /api/match
    sources/
      base.py          интерфейс Source (search / card / download)
      portal.py        адаптер Портала поставщиков
  scripts/
    refresh_snapshot.py  генерит site/data/purchases.json (для статичного фронта/cron)
  deploy/              systemd + nginx + инструкция по деплою на VPS
```

## Локальный запуск

```bash
cd server
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
# проверка:
curl 'http://localhost:8000/api/health'
curl 'http://localhost:8000/api/purchases?query=перчатки&take=5'
```

## Эндпоинты

| Метод | Путь | Что |
|---|---|---|
| GET | `/api/purchases` | лента; параметры `query, minus, law, region, stage, source, price_min, price_max, take, skip` |
| GET | `/api/purchases/{id}` | полная карточка (позиции, сроки, обеспечение, документы) |
| GET | `/api/documents/{file_id}` | скачать вложение закупки (прокси) |
| POST | `/api/match` | `multipart`: `purchase_id` + `file` (своё ТЗ) → `MatchResult` |

Поиск по ключевым словам и фильтры считаются **на нашей стороне** по пулу активных
закупок (у Портала серверный текстовый поиск капризный). Пул кэшируется (`LK_POOL_TTL`).

## Переменные окружения

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `LK_POOL_TAKE` | 200 | сколько активных закупок держать в пуле |
| `LK_POOL_TTL` | 300 | TTL кэша пула, сек |
| `LK_CORS_ORIGINS` | `*` | разрешённые origin для фронта |

## Деплой

См. [`deploy/DEPLOY.md`](deploy/DEPLOY.md) — установка на Timeweb-VPS (systemd + nginx),
c бэкапом текущего сайта Nexara перед заменой.
