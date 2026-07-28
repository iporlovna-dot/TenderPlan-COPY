// Сборщик АНАЛИТИКИ: ценовой ориентир по категориям + история заказчика —
// на основе РЕЕСТРА КОНТРАКТОВ ЕИС (заключённые контракты, реальная цена, а не
// НМЦК активных закупок). Отдельный от build_snapshot.js источник данных: там —
// активные закупки (лента), здесь — исторические заключённые контракты (аналитика).
//
//   node tools/build_analytics.js       // пишет site/data/analytics.json
//
// Категории — те же темы, что и в прицельном поиске ЕИС (tools/sources/eis.js
// DEFAULT_KEYWORDS), чтобы фронт мог сопоставлять закупку с категорией той же
// stem-логикой, что уже используется в поиске (appview.js).
//
// Честное ограничение: реестр контрактов в списке не показывает ИНН заказчика и
// имя поставщика-победителя (только текстом заказчика и ценой) — см. CLAUDE.md.
// История заказчика поэтому строится по нормализованному ИМЕНИ (без ИНН), это
// приближение: разные заказчики с похожим названием теоретически могут схлопнуться.
// Поставщик-победитель и число участников (уровень конкуренции) в эту версию не
// входят — требуют захода в карточку каждого контракта/протокол закупки отдельно,
// не покрыто.

const fs = require("fs");
const path = require("path");

const { collectContractsByKeyword } = require("./sources/eisContracts");
const { DEFAULT_KEYWORDS } = require("./sources/eis");

const OUT_DIR = path.resolve(__dirname, "..", "site", "data");
const OUT = path.join(OUT_DIR, "analytics.json");

const PER_KEYWORD_TAKE = Number(process.env.LK_ANALYTICS_TAKE || 100);
const KEYWORDS = process.env.LK_ANALYTICS_KEYWORDS
  ? process.env.LK_ANALYTICS_KEYWORDS.split(",").map(s => s.trim()).filter(Boolean)
  : DEFAULT_KEYWORDS;

function median(sorted) {
  const n = sorted.length;
  if (!n) return 0;
  const mid = Math.floor(n / 2);
  return n % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function normalizeCustomerName(s) {
  return (s || "")
    .toUpperCase()
    .replace(/[«»"'ʼ]/g, "")
    .replace(/[^\wА-ЯЁ0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function buildCategoryStats(itemsByKeyword) {
  const categories = {};
  for (const [kw, items] of Object.entries(itemsByKeyword)) {
    const prices = items.map(it => it.price).filter(p => p > 0).sort((a, b) => a - b);
    if (!prices.length) { categories[kw] = { count: 0 }; continue; }
    categories[kw] = {
      count: items.length,
      avgPrice: Math.round(prices.reduce((s, p) => s + p, 0) / prices.length),
      medianPrice: Math.round(median(prices)),
      minPrice: Math.round(prices[0]),
      maxPrice: Math.round(prices[prices.length - 1]),
    };
  }
  return categories;
}

function buildCustomerStats(itemsByKeyword) {
  // глобальный dedup по reestrNumber — один и тот же контракт может попасться под
  // несколькими темами (например «перчатки» и «медицинские изделия» одновременно),
  // без этого его цена посчиталась бы в историю заказчика дважды
  const seen = new Set();
  const byName = new Map();
  for (const items of Object.values(itemsByKeyword)) {
    for (const it of items) {
      if (seen.has(it.reestrNumber)) continue;
      seen.add(it.reestrNumber);
      const key = normalizeCustomerName(it.customer);
      if (!key) continue;
      let rec = byName.get(key);
      if (!rec) {
        rec = { displayName: it.customer.trim(), count: 0, totalPrice: 0, minDate: it.date, maxDate: it.date };
        byName.set(key, rec);
      }
      rec.count++;
      rec.totalPrice += it.price;
      if (it.date && (!rec.minDate || it.date < rec.minDate)) rec.minDate = it.date;
      if (it.date && (!rec.maxDate || it.date > rec.maxDate)) rec.maxDate = it.date;
    }
  }
  const customers = {};
  for (const [key, rec] of byName.entries()) {
    customers[key] = {
      displayName: rec.displayName,
      count: rec.count,
      totalPrice: Math.round(rec.totalPrice),
      avgPrice: Math.round(rec.totalPrice / rec.count),
      minDate: rec.minDate, maxDate: rec.maxDate,
    };
  }
  return customers;
}

async function main() {
  console.log(`Реестр контрактов: собираю ${KEYWORDS.length} тем по ${PER_KEYWORD_TAKE} контрактов…`);
  const itemsByKeyword = await collectContractsByKeyword(KEYWORDS, PER_KEYWORD_TAKE);

  const categories = buildCategoryStats(itemsByKeyword);
  const customers = buildCustomerStats(itemsByKeyword);

  const totalContracts = Object.values(itemsByKeyword).reduce((s, arr) => s + arr.length, 0);
  const payload = {
    generatedAt: new Date().toISOString(),
    source: "Реестр контрактов ЕИС (zakupki.gov.ru) — заключённые контракты, выборка по темам",
    note: "Ценовой ориентир и история заказчика — по выборке контрактов, совпавших с темами поиска ниже; не полный реестр (51+ млн записей). Число участников/уровень конкуренции не собирается — не найдено в списке контрактов, требует захода в протокол каждой закупки.",
    keywords: KEYWORDS,
    perKeywordTake: PER_KEYWORD_TAKE,
    totalContracts,
    categories,
    customers,
  };
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(payload, null, 2), "utf8");
  console.log(`wrote ${totalContracts} контрактов -> ${Object.keys(categories).length} категорий, ${Object.keys(customers).length} заказчиков -> ${OUT}`);
}

main();
