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
| `tools/build_analytics.js` | собирает реестр контрактов по темам → `site/data/analytics.json` (ценовой ориентир, история заказчика) |
| `tools/sources/eisContracts.js` | адаптер реестра контрактов ЕИС (для аналитики, не для ленты) |
| `tools/refresh.sh` | автообновление: пересобрать → scp на VPS (закупки — если состав изменился; аналитика — раз в сутки) |
| `tools/refresh.cmd` | обёртка для Планировщика Windows |
| `server/` | FastAPI-бэкенд (отложен) + `deploy/` (systemd, nginx, DEPLOY.md) |
| `matcher/` | движок сверки SpecMatch — импортирован сюда с историей (см. ниже) |

### `matcher/` — движок сверки ТЗ↔товар (импорт SpecMatch)

Отдельный проект `tender-matcher` (он же SpecMatch), влит в этот репо через
`git subtree` вместе с **96 коммитами** — до импорта его история существовала
только на одной машине, без remote. Импортирована ветка
`videolaryngoscope-bd-df-besdata`, а не `main`: `main` там отстал на 44 коммита,
вся работа за последний месяц (api, миграции, кабинет, сборные лоты) лежала на
фича-ветке.

⚠️ **История доступна, но не по пути.** `git log -- matcher/` покажет только сам
импорт: в старых коммитах пути идут от корня (`src/matcher.py`, не
`matcher/src/matcher.py`) — так работает `subtree add`. Смотреть через точку
импорта: `git log 92c3a77 -- src/matcher.py`.

| Внутри `matcher/` | Что | Статус |
|---|---|---|
| `src/matcher.py` (457 стр.) | ядро: операторы `≥/≤/range/one_of/set`, веса, дисквалификация | **живое, 39/39 тестов** |
| `src/schema.py` | `Product` · `Requirement` · `Check` · `MatchResult` | живое |
| `src/parser.py` | `.docx/.pdf/.xlsx/.xls` → текст **+ таблицы**, OCR-fallback, архивы | живое |
| `src/filter.py`, `ktru.py` | воронка: ОКПД2/КТРУ, слова, срок, регион | живое |
| `src/keymatch.py`, `extractor.py`, `embed.py` | семантика и извлечение требований | нужен `numpy` / `ANTHROPIC_API_KEY` |
| `src/tenderplan.py` | адаптер Тендерплана | **мёртвый груз** — от Тендерплана ушли |
| `api/`, `web/`, `migrations/` | свой FastAPI + кабинет + alembic | **архив-референс**, дублирует `server/` и `site/`, не деплоится |
| `tests/` | 17 файлов, включая golden | 39/39 юнит + 46/46 golden (23 фикстуры) |

Ядро **не требует зависимостей вообще** — `matcher/src` на `sys.path` и
`import matcher` работает на голом Python 3.12. Тяжёлое (`sentence-transformers`,
`easyocr` → torch, ~950 МБ venv) нужно только опциональным слоям; на VPS его не тащить.

**Стыковка с Лекало ещё не сделана.** Схемы совместимы (`verdict` совпадает
дословно, `Check.status`: у Лекало `fail` — у SpecMatch `violation`), но зазор
содержательный: `/api/match` в Лекало сверяет **текст↔текст**, а
`matcher.match()` принимает **структуру** — `Product` (карточка товара с
атрибутами; в Лекало таких карточек нет) и `List[Requirement]` (извлекаются
LLM-ом). Порядок сближения — в [`plan.md`](plan.md).

### Схема закупки (единая для всех площадок)
`{ id, number, title, customer, customerInn, law, source, region, okpd, price,
stage, endDate(ISO), beginDate, deadlineDays, publishedDaysAgo, guaranteeApp,
guaranteeContract, prepayment, href, deliveryDays, deliveryPlace,
lots[{name,qty,price,okpd}], documents[{id,name,url}], matches{productId→MatchResult} }`

---

## Источники (проверенные эндпоинты, анонимно)

**Реестр контрактов ЕИС (zakupki.gov.ru)** — источник аналитики (не активных закупок):
- список: `GET epz/contract/search/results.html?searchString=<текст>&recordsPerPage=_50&pageNumber=N`
  (те же блоки `search-registry-entry-block`: заказчик, предмет, **реальная цена контракта**, дата)
- **НЕ глушится антиботом для searchString** (в отличие от поиска активных закупок ниже) — обычный
  curl без Chromium, проверено заведомо-бессмысленным словом (честные 0). Важно: без `-L` (follow
  redirect) — с ним почему-то молча теряет searchString и отдаёт 0.
- ИНН заказчика и имя поставщика-победителя в списке **не показаны** (пустой `inn=` в ссылке на
  заказчика) — победитель только в карточке `contractCard/...`, отдельный заход, не собираем.
- Число участников/уровень конкуренции — не нашли нигде в списке, вероятно только в протоколе
  подведения итогов конкретной закупки; **не реализовано**, честно написано в UI, а не выдумано.

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
node tools/build_snapshot.js         # пишет site/data/purchases.json (~5-6 мин)
scp -i ~/.ssh/nexara_deploy site/data/purchases.json root@186.246.30.213:/opt/lekalo/site/data/purchases.json.tmp
ssh -i ~/.ssh/nexara_deploy root@186.246.30.213 'mv /opt/lekalo/site/data/purchases.json{.tmp,}'
```
Или разом: `bash tools/refresh.sh` (доставляет по scp только если изменился состав).

**Снапшот больше НЕ коммитится в git** (см. «Грабли» — на ~4000+ закупках это ~7 МБ,
раздуло бы историю). `.gitignore` его исключает; на диске файл остаётся как обычно,
просто git его не отслеживает. Код (`site/js`, `server/`, `tools/`) — по-прежнему
через обычный `git push` + `ssh … git pull` на VPS, вручную.

**Автообновление:** Планировщик Windows `LekaloSnapshotRefresh` запускает `refresh.cmd` каждый
час (лог `C:\Users\nikit\lekalo-refresh.log`). Управление: `schtasks /Run|/Change /TN LekaloSnapshotRefresh`.
Проверено: интервал ровно `PT1H`, без пропусков.

**Тюнинг объёма (env):** `LK_SNAPSHOT_TAKE` (Портал, деф. 500), `LK_EIS_TAKE` (ЕИС общий список, 2000),
`LK_EIS_DOCS` (для скольких ЕИС качать документы/регион, 150), `LK_EIS_KEYWORDS`
(свой список тем через запятую вместо ~30 по умолчанию), `LK_EIS_KEYWORD_TAKE` (закупок на тему, 60),
`LK_POOL`, `LK_PORTAL_CONC`, `LK_EIS_CONC`. Прицельный поиск по темам ЕИС идёт через headless-Chromium
(Playwright), не curl — источник глушит поиск для голых HTTP-клиентов (см. «Грабли»).

---

## VPS (Timeweb) — деплой

- IP `186.246.30.213`, ключ `C:/Users/nikit/.ssh/nexara_deploy`, `root`. nginx отдаёт
  `/opt/lekalo/site` (это git-клон репо), сайт `lekalo` включён.
- Заменили статику **Nexara** (это отдельный проект!). Бэкап: `/root/backups/nexara-*.tgz`.
  Откат — см. `server/deploy/DEPLOY.md`.
- Деплой правок = `git push` + `ssh … 'cd /opt/lekalo && git pull --ff-only'`.
- Живой сайт: **`https://186.246.30.213.nip.io/`** (HTTPS, Let's Encrypt через nip.io — см.
  `server/deploy/enable-https.sh`). Голый IP `http://186.246.30.213/` тоже работает для
  ленты/админки (старые ссылки не ломаются), но вход/регистрация/кабинет с него редиректят
  на HTTPS-домен — secure-cookie сессии по голому HTTP браузер не сохранит.

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
6. **Снапшот в git больше не коммитится** (было 2.1 МБ ежечасно, на ~4000+ закупках выросло бы
   до ~7 МБ и раздуло историю) — доставляется на VPS напрямую по `scp` (`tools/refresh.sh`),
   `.gitignore` файл исключает. На диске он есть как обычно, просто не отслеживается.
7. **`sites-enabled/lekalo` может незаметно перестать быть симлинком** на
   `sites-available/lekalo` (разово случилось 2026-07-28, вероятно из-за ручной правки
   или certbot) — тогда правки конфига (например, `enable-ratelimit.sh`) уходят в файл,
   который nginx НЕ грузит: `nginx -t`/`reload` проходят успешно, но поведение не
   меняется (лимит стоял в конфиге, а 200 быстрых запросов подряд ни разу не поймали
   429). После любой правки nginx-конфига проверять: `ls -la /etc/nginx/sites-enabled/lekalo`
   должен показывать `-> /etc/nginx/sites-available/lekalo` (симлинк), а не `-rw-r--r--`
   (независимый файл). `enable-ratelimit.sh` теперь сам чинит симлинк при расхождении.
8. **Поиск по ключевым словам (searchString) ЕИС глушится для «голых» HTTP-клиентов** —
   curl с этой машины получает «0 записей» на любой поисковый запрос (проверено заведомо
   новыми словами), а тот же запрос с той же машины/IP через headless-Chromium (Playwright) —
   реальные результаты. Не IP-бан, а разбор клиента (TLS/JS-отпечаток). Поэтому
   `collectEis`'s прицельный поиск по темам идёт через Playwright, а не curl (общий список
   без поиска — по-прежнему curl, там не глушит). Не пытаться обходить проксями/спуфингом.

---

## Git

Ветка `main`. **Не коммитить:** секреты (`.env`, токены), `certs/`, `scratchpad/`, `server/.venv/`
(см. `.gitignore`). Коммит-сообщения заканчивать:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
Пуш/деплой данных — часть рабочего цикла обновления (см. выше); правки кода — по договорённости.
