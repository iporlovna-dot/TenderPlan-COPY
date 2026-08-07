// Сборщик снапшота: собирает закупки со ВСЕХ подключённых площадок и сводит
// в один site/data/purchases.json (единая схема). Запускать НА МАШИНЕ, где
// доступны источники (гос-сайты блокируют дата-центры!). Node 18+, curl.
//
//   node tools/build_snapshot.js
//   git add site/data/purchases.json && git commit -m "refresh snapshot" && git push
//
// Площадки — модули в tools/sources/*, каждый экспортирует collect...().

const fs = require("fs");
const path = require("path");

const { collectPortal } = require("./sources/portal");
const { collectEis } = require("./sources/eis");
const { annotateTz } = require("./sources/tzterms");

const OUT_DIR = path.resolve(__dirname, "..", "site", "data");
const OUT = path.join(OUT_DIR, "purchases.json");

// Кэш карточек ЕИС (документы/регион/срок поставки), переживающий пересборку.
// Лежит вне site/ намеренно: это рабочий файл сборщика, он не деплоится и не
// коммитится (.gitignore). Без него каждый час заново качались бы те же 150
// карточек, а покрытие ленты документами навсегда упиралось бы в LK_EIS_DOCS.
const CACHE_DIR = path.resolve(__dirname, ".cache");
const DOCS_CACHE = path.join(CACHE_DIR, "eis-docs.json");
// Термины ТЗ (для «Умной сверки») — свой кэш: ТЗ у закупки не меняется от часа к
// часу, а качать документы каждый прогон заново — гигабайты трафика впустую.
const TZ_CACHE = path.join(CACHE_DIR, "tz-terms.json");
// Бюджет разбора ТЗ за прогон. Держать ВЫШЕ EIS_DOCS: документы прибывают по
// EIS_DOCS за час и каждый становится кандидатом на разбор, так что при меньшем
// бюджете очередь растёт быстрее, чем разгребается, и покрытие не сходится.
// Проверено на живом прогоне 2026-08-07: 120 против 150 — очередь копилась.
const TZ_BUDGET = Number(process.env.LK_TZ_DOCS || 220);

function loadCache(file) {
  try { return JSON.parse(fs.readFileSync(file, "utf8")); }
  catch (e) { return {}; }   // нет файла/битый — начинаем с чистого, это не ошибка
}

function loadDocsCache() { return loadCache(DOCS_CACHE); }

// Чистим кэш от закупок, которых больше нет в снапшоте (истёк срок подачи и т.п.),
// иначе он растёт без предела и тащит мусор годами.
function saveCache(file, cache, keep) {
  for (const n of Object.keys(cache)) if (!keep.has(n)) delete cache[n];
  fs.mkdirSync(CACHE_DIR, { recursive: true });
  fs.writeFileSync(file, JSON.stringify(cache), "utf8");
  return Object.keys(cache).length;
}

function saveDocsCache(cache, keepNumbers) { return saveCache(DOCS_CACHE, cache, keepNumbers); }

const PORTAL_TAKE = Number(process.env.LK_SNAPSHOT_TAKE || 500);
// общий список (без поиска по словам) — сейчас единственный канал ЕИС, который
// не задет антибот-мерой на поиск (см. plan.md); подняли повыше как страховку,
// чтобы редкие темы всё же попадали в снапшот хоть по объёму, если поиск глушится
const EIS_LIST = Number(process.env.LK_EIS_TAKE || 2000);
const EIS_DOCS = Number(process.env.LK_EIS_DOCS || 150);   // для скольких ближайших качать документы
// прицельные проходы по темам (полнотекстовый поиск ЕИС), поверх общего списка —
// иначе узкие темы почти не попадают в топ «последних обновлённых по всей РФ»
const EIS_KEYWORDS = process.env.LK_EIS_KEYWORDS
  ? process.env.LK_EIS_KEYWORDS.split(",").map(s => s.trim()).filter(Boolean)
  : undefined;  // undefined -> collectEis возьмёт свой список категорий по умолчанию

function endTs(p) { return p.endDate ? new Date(p.endDate).getTime() : Infinity; }

async function main() {
  let purchases = [];

  const docsCache = loadDocsCache();

  // обе площадки — параллельно
  const [portal, eis] = await Promise.all([
    collectPortal(PORTAL_TAKE).catch(e => (console.error("Портал: ошибка —", e.message), [])),
    collectEis(EIS_LIST, EIS_DOCS, EIS_KEYWORDS, docsCache)
      .catch(e => (console.error("ЕИС: ошибка —", e.message), [])),
  ]);

  // сохраняем кэш до фильтра по сроку: закупка, отсеянная как просроченная в этом
  // прогоне, из кэша и так уйдёт, а вот сбой ЕИС (eis=[]) не должен стереть всё
  // накопленное — при пустом ответе кэш оставляем как есть
  if (eis.length) {
    const kept = saveDocsCache(docsCache, new Set(eis.map(p => p.number)));
    console.log(`  кэш карточек ЕИС: ${kept} записей -> ${path.relative(process.cwd(), DOCS_CACHE)}`);
  }
  purchases = purchases.concat(portal, eis);

  // убрать уже просроченные (на всякий случай) и отсортировать по близости дедлайна
  const now = Date.now();
  purchases = purchases
    .filter(p => !p.endDate || new Date(p.endDate).getTime() > now)
    .sort((a, b) => endTs(a) - endTs(b));

  // Термины ТЗ — после фильтра по сроку: качать документы закупки, которая уже
  // закрылась, бессмысленно. Сбой здесь не должен ронять снапшот: лента без
  // «Умной сверки» полезна, а сверка без ленты — нет.
  const tzCache = loadCache(TZ_CACHE);
  try {
    await annotateTz(purchases, tzCache, TZ_BUDGET);
    const kept = saveCache(TZ_CACHE, tzCache, new Set(purchases.map(p => p.id)));
    console.log(`  кэш терминов ТЗ: ${kept} записей -> ${path.relative(process.cwd(), TZ_CACHE)}`);
  } catch (e) {
    console.error("ТЗ: ошибка разбора —", e.message, "(снапшот пишу без терминов)");
  }

  const bySrc = purchases.reduce((m, p) => (m[p.source] = (m[p.source] || 0) + 1, m), {});
  const withDocs = purchases.filter(p => p.documents && p.documents.length).length;
  const withTz = purchases.filter(p => p.tzTerms && p.tzTerms.length).length;

  const payload = {
    generatedAt: new Date().toISOString(),
    source: "Портал поставщиков + ЕИС (zakupki.gov.ru) — активные закупки",
    total: purchases.length,
    purchases,
  };
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(payload, null, 2), "utf8");
  console.log(`wrote ${purchases.length} закупок (${JSON.stringify(bySrc)}), `
    + `${withDocs} с документами, ${withTz} с терминами ТЗ -> ${OUT}`);
}

main();
