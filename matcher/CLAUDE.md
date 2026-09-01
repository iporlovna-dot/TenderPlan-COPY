# CLAUDE.md — SpecMatch (tender-matcher)

> Проектная памятка. Глобальные правила (про скиллы) грузятся отдельно из
> `~/.claude/CLAUDE.md` — здесь их НЕ дублируем. Статус/дорожная карта — в `plan.md`,
> здесь только специфика проекта: правило синка, карта модулей, запуск, git.

**SpecMatch** — сервис подбора госзакупок (44-ФЗ/223-ФЗ) под товар поставщика по
**фактическим характеристикам ТЗ**, а не по классификаторам ОКПД2/КТРУ. Полное описание,
архитектура, статус и этапы — в [`plan.md`](plan.md) (Часть I — действия и приоритеты).

---

## Правило: синхронизация с plan.md

`plan.md` — источник истины по замыслу и статусу. Структура: **Часть I** (действия/приоритеты)
сверху, **Часть II** (справка) ниже. В начале сессии читаю Часть I и этот файл. **После каждой
завершённой и проверенной правки** сразу отмечаю сделанное в `plan.md`: §2 «Сделано», §3 «Прямо
сейчас», §4 «Этапы» — `[x]` сделано / `[~]` в работе / `[ ]` нет. Не «авансом». Состояние живёт
в файлах, а не в контексте — это спасает при заполнении контекста.

---

## Карта модулей (`src/`)

| Файл | Что делает | LLM |
|---|---|---|
| `schema.py` | модели: `Product`, `Requirement`, `MatchResult`, `Purchase` (+`company_id`) | нет |
| `matcher.py` | ядро: операторы, скоринг, дисквалификация, гомоглифы кир/лат | нет |
| `filter.py` | воронка-фильтры: ОКПД2/КТРУ + слова + срок подачи + регион (шаг 2) | нет |
| `ktru.py` | сверка КТРУ товар↔позиция закупки с градацией `exact`/`group`/`none` (шаг 2) | нет |
| `keymatch.py` | семантика ТЗ↔карточка до матчера: `align_keys` (имена полей) + `align_values` (значения) | Anthropic/DeepSeek (`field_equivalence`) |
| `parser.py` | `.docx/.pdf/.xlsx/.txt` → текст + таблицы (шаг 5); PDF-скан → OCR-fallback | нет |
| `ocr.py` | OCR сканов-PDF: tesseract → easyocr (шаг 5) | нет |
| `models.py` | маршрутизация моделей по задаче И по провайдеру (`pick_model`, см. `llmclient.py`) | — |
| `llmclient.py` | провайдер-агностичный LLM-клиент: выбор по `LK_LLM_PROVIDER` (деф. Anthropic) + единый `structured_create` (Anthropic constrained-JSON / OpenAI-совместимый json_object) | — |
| `extractor.py` | извлечение требований из ТЗ (шаг 6) — ждёт ключ активного провайдера | Anthropic/DeepSeek (`extract`) |
| `source.py` | интерфейс источника закупок (заменяемый адаптер) | нет |
| `tenderplan.py` | адаптер Тендерплана: discovery по фиду + забор ТЗ с zakupki | нет |

⚠️ **Провайдер по умолчанию — Anthropic** (снова, с 2026-09-01: прод переехал в
NL-регион Timeweb, где Anthropic не блокируется — см. корневой `CLAUDE.md`,
раздел «Anthropic API — гео-блок», там же история блокировки на прежнем
RU-хостинге). `LK_LLM_PROVIDER=deepseek` включает DeepSeek одной переменной
(ключ — `DEEPSEEK_API_KEY`), без переписывания кода — код и тесты под него
сохранены на случай повторного переезда/блокировки. Диспетчеризация вызова
(`llmclient.structured_create`) идёт по ФОРМЕ клиента (`.messages` vs `.chat`),
не по этому флагу напрямую — поэтому юнит-тесты (моки Anthropic-формы)
продолжают работать независимо от прод-дефолта.

`scripts/`: `run_match.py` (товар+требования→вердикт) · `run_pipeline.py` (документ→вердикт) ·
`run_auto.py` (сквозной автопрогон, флаг `--collect-only` работает без API) ·
`tp_probe.py` (снять схемы Тендерплана) · `setup_ca_bundle.py` (CA-бандл Минцифры).

**Инвариант:** LLM в ядро (`matcher.py`) не заходит — сюда приходит уже структура.
Мультиарендность (`company_id`) живёт на уровне БД/API, не в ядре.

---

## Запуск

```bash
# зависимости
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# ядро на готовых требованиях (без LLM)
.venv/bin/python scripts/run_match.py data/products/gloves_nitrile.json \
    data/golden/gloves_med_6a5f0d4e.json data/profiles/gloves.json

# автосбор ТЗ по перчаткам (на подписке Тендерплана, без Anthropic)
export TENDERPLAN_TOKEN=...            # PAT со scope resources+keys+relations+marks
.venv/bin/python scripts/setup_ca_bundle.py            # один раз: CA-бандл zakupki
.venv/bin/python scripts/run_auto.py data/profiles/gloves.json data/products/ \
    --collect-only --out scratchpad/tz_texts

# тесты
.venv/bin/python tests/test_matcher.py && .venv/bin/python tests/test_filter.py
```

Ключи в окружении, не в коде и не в чате: `TENDERPLAN_TOKEN`, `ANTHROPIC_API_KEY`
(активный провайдер по умолчанию) / `DEEPSEEK_API_KEY` (нужен только при
`LK_LLM_PROVIDER=deepseek`, см. таблицу модулей выше).

---

## Git

Ветка по умолчанию — `main`. **Не коммитить:** секреты (`.env`, токены), `certs/`,
`scratchpad/`, скачанные ТЗ с ПДн. Коммитить/пушить — только по явной просьбе.
