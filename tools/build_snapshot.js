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

const OUT_DIR = path.resolve(__dirname, "..", "site", "data");
const OUT = path.join(OUT_DIR, "purchases.json");

const PORTAL_TAKE = Number(process.env.LK_SNAPSHOT_TAKE || 400);
const EIS_LIST = Number(process.env.LK_EIS_TAKE || 600);   // сколько ЕИС тянуть списком (дёшево)
const EIS_DOCS = Number(process.env.LK_EIS_DOCS || 150);   // для скольких ближайших качать документы

function endTs(p) { return p.endDate ? new Date(p.endDate).getTime() : Infinity; }

async function main() {
  let purchases = [];

  // обе площадки — параллельно
  const [portal, eis] = await Promise.all([
    collectPortal(PORTAL_TAKE).catch(e => (console.error("Портал: ошибка —", e.message), [])),
    collectEis(EIS_LIST, EIS_DOCS).catch(e => (console.error("ЕИС: ошибка —", e.message), [])),
  ]);
  purchases = purchases.concat(portal, eis);

  // убрать уже просроченные (на всякий случай) и отсортировать по близости дедлайна
  const now = Date.now();
  purchases = purchases
    .filter(p => !p.endDate || new Date(p.endDate).getTime() > now)
    .sort((a, b) => endTs(a) - endTs(b));

  const bySrc = purchases.reduce((m, p) => (m[p.source] = (m[p.source] || 0) + 1, m), {});
  const withDocs = purchases.filter(p => p.documents && p.documents.length).length;

  const payload = {
    generatedAt: new Date().toISOString(),
    source: "Портал поставщиков + ЕИС (zakupki.gov.ru) — активные закупки",
    total: purchases.length,
    purchases,
  };
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(payload, null, 2), "utf8");
  console.log(`wrote ${purchases.length} закупок (${JSON.stringify(bySrc)}), ${withDocs} с документами -> ${OUT}`);
}

main();
