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
const {
  mergeWindow, evictStore, refreshVolatile, sortedPurchases, loadStore, saveStore,
} = require("./sources/store");

const OUT_DIR = path.resolve(__dirname, "..", "site", "data");
const OUT = path.join(OUT_DIR, "purchases.json");

// Кэш карточек ЕИС (документы/регион/срок поставки), переживающий пересборку.
// Лежит вне site/ намеренно: это рабочий файл сборщика, он не деплоится и не
// коммитится (.gitignore). Без него каждый час заново качались бы те же 150
// карточек, а покрытие ленты документами навсегда упиралось бы в LK_EIS_DOCS.
const CACHE_DIR = path.resolve(__dirname, ".cache");
const DOCS_CACHE = path.join(CACHE_DIR, "eis-docs.json");
// Накопитель активных закупок: закупка живёт в нём до истечения срока подачи, а
// окно ЕИС только доливает новое. Зачем он и что было до него — в sources/store.js.
const STORE = path.join(CACHE_DIR, "store.json");
// Термины ТЗ (для «Умной сверки») — свой кэш: ТЗ у закупки не меняется от часа к
// часу, а качать документы каждый прогон заново — гигабайты трафика впустую.
const TZ_CACHE = path.join(CACHE_DIR, "tz-terms.json");
// Бюджет разбора ТЗ за прогон. Держать ВЫШЕ EIS_DOCS: документы прибывают по
// EIS_DOCS за час и каждый становится кандидатом на разбор, так что при меньшем
// бюджете очередь растёт быстрее, чем разгребается, и покрытие не сходится.
// Проверено на живом прогоне 2026-08-07: 120 против 150 — очередь копилась.
const TZ_BUDGET = Number(process.env.LK_TZ_DOCS || 220);

// Версия алгоритма отбора терминов ТЗ. Кэш хранит уже ОТОБРАННЫЕ термины, а не
// текст документа, поэтому при смене отбора старые записи остаются старыми
// навсегда — пока кто-нибудь не удалит файл руками. А руками некому: сборщик
// живёт на другой машине и ходит по расписанию. Поэтому версия лежит в самом
// кэше, и при расхождении он выбрасывается сам.
// ПОДНИМАТЬ при любом изменении topTerms / termFreq / NUM_RE.
const TZ_ALGO = 4;
const ALGO_KEY = "__algo";

// Статусы разбора ТЗ, означающие «вопрос закрыт»: документ скачан и что-то про
// него теперь известно окончательно. Ровно их и кладёт в кэш annotateTz.
// `pending` («не дошла очередь») и `no-doc` («документов ещё не видели») сюда НЕ
// входят: они говорят о состоянии сборщика, а не о закупке, и, попав в кэш,
// намертво закрыли бы ей дорогу к разбору.
const TZ_FINAL = new Set(["ok", "unsupported", "empty", "error"]);

function loadCache(file, algo) {
  try {
    const cache = JSON.parse(fs.readFileSync(file, "utf8"));
    if (algo != null && cache[ALGO_KEY] !== algo) {
      console.log(`  кэш ${path.basename(file)}: алгоритм отбора изменился — разбираю заново`);
      return {};
    }
    delete cache[ALGO_KEY];
    return cache;
  } catch (e) { return {}; }   // нет файла/битый — начинаем с чистого, это не ошибка
}

function loadDocsCache() { return loadCache(DOCS_CACHE); }

// Чистим кэш от закупок, которых больше нет в снапшоте (истёк срок подачи и т.п.),
// иначе он растёт без предела и тащит мусор годами.
function saveCache(file, cache, keep, algo) {
  for (const n of Object.keys(cache)) if (n !== ALGO_KEY && !keep.has(n)) delete cache[n];
  if (algo != null) cache[ALGO_KEY] = algo;
  fs.mkdirSync(CACHE_DIR, { recursive: true });
  fs.writeFileSync(file, JSON.stringify(cache), "utf8");
  return Object.keys(cache).length - (algo != null ? 1 : 0);
}

function saveDocsCache(cache, keepNumbers) { return saveCache(DOCS_CACHE, cache, keepNumbers); }

// Сколько закупок уезжает в purchases.json. Намеренно ОТДЕЛЬНО от размера
// накопителя: собирать можно сколько угодно, а фронт грузит и разбирает файл
// целиком, и на 20 тыс. закупок с терминами это ~89 МБ (замер по прод-снапшоту:
// 4689 Б на закупку с ТЗ). Пока доставка не разделена на ленту и термины,
// накопитель растёт, а в снапшот уходят ближайшие по дедлайну.
const SNAPSHOT_MAX = Number(process.env.LK_SNAPSHOT_MAX || 6000);

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

async function main() {
  let purchases = [];

  const docsCache = loadDocsCache();

  // обе площадки — параллельно
  const [portal, eis] = await Promise.all([
    collectPortal(PORTAL_TAKE).catch(e => (console.error("Портал: ошибка —", e.message), [])),
    collectEis(EIS_LIST, EIS_DOCS, EIS_KEYWORDS, docsCache)
      .catch(e => (console.error("ЕИС: ошибка —", e.message), [])),
  ]);

  // Свежее окно вливаем в накопитель, а не заменяем им состав. Закупка, которой
  // в этот час не оказалось в окне, остаётся жить со всем добытым.
  const now = Date.now();
  const store = loadStore(STORE, OUT);
  const fresh = portal.concat(eis);
  const added = mergeWindow(store, fresh, now);
  const ev = evictStore(store, now);
  purchases = sortedPurchases(store);
  console.log(`  накопитель: ${purchases.length} закупок (в окне ${fresh.length}, из них новых ${added}; `
    + `выселено: истекло ${ev.expired}, без срока и старше TTL ${ev.stale}, сверх потолка ${ev.overflow})`);

  // Кэши чистим по составу НАКОПИТЕЛЯ, а не снапшота. Раньше keep-множество
  // строилось по снапшоту, и добытые документы/термины закупки удалялись, стоило
  // ей выпасть из вращающегося окна — при том что сама закупка была ещё активна.
  // Сбой ЕИС (eis=[]) кэш не тронет и так: записи ЕИС остаются в накопителе.
  if (eis.length) {
    const eisNumbers = new Set(purchases.filter(p => p.number && String(p.id).startsWith("eis_"))
      .map(p => p.number));
    const kept = saveDocsCache(docsCache, eisNumbers);
    console.log(`  кэш карточек ЕИС: ${kept} записей -> ${path.relative(process.cwd(), DOCS_CACHE)}`);
  }

  // Термины ТЗ — по всему накопителю: качать документы закупки, которая уже
  // закрылась, бессмысленно, а она отсюда уже выселена. Сбой здесь не должен
  // ронять снапшот: лента без «Умной сверки» полезна, а сверка без ленты — нет.
  const tzCache = loadCache(TZ_CACHE, TZ_ALGO);
  // Записи, разобранные ТЕКУЩИМ алгоритмом, возвращаем в кэш: он мог быть
  // подчищен старым поведением или отсутствовать на этой машине, а перекачивать
  // уже разобранное — впустую. Записи без отметки версии не трогаем: чем их
  // разобрали, неизвестно, честнее разобрать заново.
  let seeded = 0;
  for (const p of purchases) {
    if (!tzCache[p.id] && p.tzAlgo === TZ_ALGO && TZ_FINAL.has(p.tzStatus)) {
      tzCache[p.id] = { terms: p.tzTerms || [], docName: p.tzDoc || "", status: p.tzStatus };
      seeded++;
    }
  }
  if (seeded) console.log(`  кэш терминов ТЗ: вернул из накопителя ${seeded} записей`);
  try {
    await annotateTz(purchases, tzCache, TZ_BUDGET);
    for (const p of purchases) if (TZ_FINAL.has(p.tzStatus)) p.tzAlgo = TZ_ALGO;
    const kept = saveCache(TZ_CACHE, tzCache, new Set(purchases.map(p => p.id)), TZ_ALGO);
    console.log(`  кэш терминов ТЗ: ${kept} записей -> ${path.relative(process.cwd(), TZ_CACHE)}`);
  } catch (e) {
    console.error("ТЗ: ошибка разбора —", e.message, "(снапшот пишу без терминов)");
  }

  // Накопитель сохраняем ПОСЛЕ разбора ТЗ — иначе добытое за этот прогон
  // пришлось бы добывать снова.
  saveStore(STORE, store);

  // В снапшот уходят ближайшие по дедлайну (см. SNAPSHOT_MAX). Накопитель при
  // этом остаётся полным: разбор ТЗ идёт по нему, так что закупка приезжает в
  // ленту уже с терминами, а не начинает знакомство с очереди.
  const stored = purchases.length;
  purchases = purchases.slice(0, SNAPSHOT_MAX);
  for (const p of purchases) refreshVolatile(p, now);

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
  console.log(`wrote ${purchases.length} закупок из ${stored} в накопителе `
    + `(${JSON.stringify(bySrc)}), ${withDocs} с документами, ${withTz} с терминами ТЗ -> ${OUT}`);
}

main();
