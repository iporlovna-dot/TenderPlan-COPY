# CLAUDE.md — Лекало (поиск торгов)

> Операционная памятка проекта. Читаю в начале каждой сессии. Дорожная карта и
> статус — в [`plan.md`](plan.md). Здесь — что это, как устроено, как обновлять
> данные и деплоить, и грабли, на которые уже наступали.

**Лекало** — собственная площадка поиска госзакупок (аналог Тендерплана/Контура):
поиск по ключевым словам, лента закупок 44/223-ФЗ со всех площадок, фильтры,
скачивание документов. Сверху — дифференциатор: сверка своего ТЗ с ТЗ закупки
(надстройка). Замысел: **независимость от чужих агрегаторов** — свой сбор данных.

> ⚠️ **Историческая справка.** Раньше проект назывался «SpecMatch» и строился как
> надстройка на API Тендерплана. От этого ушли. Если встретишь в старых заметках
> «SpecMatch / Тендерплан как источник / src/matcher.py» — это устарело.

---

## Архитектура (как есть сейчас)

**Статический снапшот + статический фронт.** Никакого живого бэкенда в проде:

```
Node-сборщики (tools/) → site/data/purchases.json → статический фронт (site/) → nginx на VPS
        ↑ гоняются на машине пользователя (гос-сайты блокируют IP дата-центров)
```

- **Фронт** (`site/`) — чистый HTML/CSS/JS, состояние в localStorage. Грузит
  `data/purchases.json` и рендерит ленту с поиском/фильтрами/пагинацией. ТЗ-сверка —
  надстройка (тумблер), на снапшоте пока без данных `matches`.
- **Сборщики** (`tools/`) — тянут закупки с площадок, нормализуют в единую схему,
  пишут `site/data/purchases.json`.
- **Бэкенд** (`server/`, FastAPI) — **написан, но в проде НЕ работает**: VPS
  заблокирован источниками (см. грабли). Оставлен на будущее (БД-бэкенд).

### Карта файлов

| Путь | Что |
|---|---|
| `site/index.html` | лендинг · `site/app.html` — приложение (лента) · `login/register.html` |
| `site/js/config.js` | `window.LK_API_BASE` — "" = статический снапшот; URL = живой API |
| `site/js/app.js` | стор `LK` (localStorage), моки-фолбэк, `loadPurchases()` (API→снапшот→моки) |
| `site/js/appview.js` | лента, поиск, фильтры, **пагинация**, живое устаревание, ТЗ-надстройка |
| `site/css/styles.css` | дизайн-система (бумага/чернила/ржавчина) |
| `site/data/purchases.json` | снапшот закупок (генерится сборщиком) |
| `tools/build_snapshot.js` | **оркестратор** — собирает все площадки, пишет снапшот |
| `tools/sources/portal.js` | адаптер Портала поставщиков (mos.ru) |
| `tools/sources/eis.js` | адаптер ЕИС (zakupki.gov.ru) |
| `tools/sources/util.js` | `curlAsync` + `mapLimit` (конкурентность) |
| `tools/refresh.sh` | автообновление: пересобрать → коммит если состав изменился → push → git pull на VPS |
| `tools/refresh.cmd` | обёртка для Планировщика Windows |
| `server/` | FastAPI-бэкенд (отложен) + `deploy/` (systemd, nginx, DEPLOY.md) |

### Схема закупки (единая для всех площадок)
`{ id, number, title, customer, customerInn, law, source, region, okpd, price,
stage, endDate(ISO), beginDate, deadlineDays, publishedDaysAgo, guaranteeApp,
guaranteeContract, prepayment, href, deliveryDays, deliveryPlace,
lots[{name,qty,price,okpd}], documents[{id,name,url}], matches{productId→MatchResult} }`

---

## Источники (проверенные эндпоинты, анонимно)

**Портал поставщиков (mos.ru)** — чистый JSON:
- поиск: `GET old.zakupki.mos.ru/api/Cssp/Purchase/Query?queryDto=<json>` (typeIn=1 КС, stateIdIn=19000002 «Активная»)
- карточка: `GET zakupki.mos.ru/newapi/api/Auction/Get?auctionId=<id>` (files[], items[], deliveries[])
- файл: `GET zakupki.mos.ru/newapi/api/FileStorage/Download?id=<fileId>`

**ЕИС (zakupki.gov.ru)** — HTML/RSS:
- список: `GET epz/order/extendedsearch/results.html?fz44=on&fz223=on&af=on&recordsPerPage=_50&pageNumber=N`
  (в блоках `search-registry-entry-block`: №, закон, предмет, заказчик, цена, срок)
- карточка (для вкладки документов): открыть `href` закупки со следованием 301
- документы: со страницы `…/documents.html` — ссылки `…/filestore/…/file.html?uid=…`

**Скачивание файлов работает из браузера напрямую** (навигация, не fetch → CORS не мешает;
браузер пользователя с жилым IP видит источник). Сервер к источникам не ходит.

---

## Как обновить данные

Сбор — **только на машине с доступом к mos.ru/zakupki.gov.ru** (не на VPS!):
```bash
node tools/build_snapshot.js         # пишет site/data/purchases.json (~1 мин)
git add site/data/purchases.json && git commit -m "refresh" && git push
ssh -i ~/.ssh/nexara_deploy root@186.246.30.213 'cd /opt/lekalo && git pull --ff-only'
```
Или разом: `bash tools/refresh.sh` (коммитит/деплоит только если изменился состав).

**Автообновление:** Планировщик Windows `LekaloSnapshotRefresh` запускает `refresh.cmd` каждый
час (лог `C:\Users\nikit\lekalo-refresh.log`). Управление: `schtasks /Run|/Change /TN LekaloSnapshotRefresh`.

**Тюнинг объёма (env):** `LK_SNAPSHOT_TAKE` (Портал, деф. 400), `LK_EIS_TAKE` (ЕИС список, 600),
`LK_EIS_DOCS` (для скольких ЕИС качать документы, 150), `LK_POOL`, `LK_PORTAL_CONC`, `LK_EIS_CONC`.

---

## VPS (Timeweb) — деплой

- IP `186.246.30.213`, ключ `C:/Users/nikit/.ssh/nexara_deploy`, `root`. nginx отдаёт
  `/opt/lekalo/site` (это git-клон репо), сайт `lekalo` включён.
- Заменили статику **Nexara** (это отдельный проект!). Бэкап: `/root/backups/nexara-*.tgz`.
  Откат — см. `server/deploy/DEPLOY.md`.
- Деплой правок = `git push` + `ssh … 'cd /opt/lekalo && git pull --ff-only'`.
- Живой сайт: `http://186.246.30.213/` (лендинг), `/app.html?demo=1` (лента). HTTP, без домена.

---

## Грабли (проверено болью)

1. **VPS заблокирован источниками.** С дата-центра `mos.ru` и `zakupki.gov.ru` не открываются
   (TCP-таймаут), хотя `ya.ru`/интернет есть. Поэтому сбор — на машине пользователя, а не на сервере.
   Из-за этого live-бэкенд на этом VPS невозможен.
2. **Локальный `python` — сломанная Store-заглушка** (Windows). Печатает «Python», ничего не
   исполняет. Для инструментов — Node. FastAPI запускать только на хосте с доступом к источникам.
3. **SSH host-key на VPS меняется после ребута** (Timeweb пересоздаёт окружение). При `REMOTE HOST IDENTIFICATION
   CHANGED` → `ssh-keygen -R 186.246.30.213` и переподключиться (это НЕ fail2ban).
4. **fail2ban на VPS:** если SSH не подключился с первой попытки (таймаут «banner exchange») —
   НЕ долбить повторно, попросить пользователя перезагрузить сервер через панель Timeweb.
5. **Cyrillic в curl-параметрах** → кракозябры. Кодировать через `curl --data-urlencode` / `--data`,
   не вставлять кириллицу прямо в URL.
6. **Снапшот 2.1 МБ коммитится ежечасно** (при смене состава) → git-история растёт. Если мешает —
   переключить доставку на прямой `scp` на VPS (без git).

---

## Git

Ветка `main`. **Не коммитить:** секреты (`.env`, токены), `certs/`, `scratchpad/`, `server/.venv/`
(см. `.gitignore`). Коммит-сообщения заканчивать:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
Пуш/деплой данных — часть рабочего цикла обновления (см. выше); правки кода — по договорённости.
