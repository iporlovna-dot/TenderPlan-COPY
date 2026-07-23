// Сборщик снапшота закупок для статичного фронта.
// Запускать НА МАШИНЕ, где доступен mos.ru (дата-центры гос-сайты блокируют!).
// Node 18+, использует системный curl. Пишет site/data/purchases.json.
//
//   node tools/build_snapshot.js
//   git add site/data/purchases.json && git commit -m "refresh snapshot" && git push
//   (затем на VPS: git pull — или попроси меня подтянуть)

const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const OUT_DIR = path.resolve(__dirname, "..", "site", "data");
const OUT = path.join(OUT_DIR, "purchases.json");

const BASE = "https://old.zakupki.mos.ru/api/Cssp/Purchase/Query";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36";
const TAKE = Number(process.env.LK_SNAPSHOT_TAKE || 48);

const queryDto = JSON.stringify({
  filter: { typeIn: { values: [1] }, auctionSpecificFilter: { stateIdIn: [19000002] } },
  order: [{ field: "endDate", desc: false }],
  withCount: true, take: TAKE, skip: 0
});

function fetchRaw() {
  const out = execFileSync("curl", [
    "-s", "-G", "-A", UA, "--max-time", "40",
    "--data-urlencode", "queryDto=" + queryDto, BASE
  ], { maxBuffer: 20 * 1024 * 1024, encoding: "utf8" });
  return JSON.parse(out);
}

const DAY = 86400000;
function parseDate(s) {
  const m = /^(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2}):(\d{2})/.exec(s || "");
  return m ? new Date(+m[3], +m[2] - 1, +m[1], +m[4], +m[5], +m[6]) : null;
}

function map(it) {
  const now = new Date();
  const end = parseDate(it.endDate), begin = parseDate(it.beginDate);
  const cust = (it.customers && it.customers[0]) || {};
  const price = Number(it.startPrice) || 0;
  return {
    id: "pp_" + it.auctionId,
    number: String(it.number || it.auctionId),
    title: it.name || "Котировочная сессия",
    customer: cust.name || "—",
    customerInn: cust.inn || "",
    law: it.federalLawName || "44-ФЗ",
    source: "Портал поставщиков (mos.ru)",
    region: (it.regionName || "").replace(/^г\s+/, "г. "),
    okpd: "",
    price,
    stage: it.stateId === 19000002 ? "active" : (end && end > now ? "active" : "committee"),
    deadlineDays: end ? Math.max(0, Math.ceil((end - now) / DAY)) : 0,
    publishedDaysAgo: begin ? Math.max(0, Math.round((now - begin) / DAY)) : 0,
    guaranteeApp: 0, guaranteeContract: 0, prepayment: 0,
    href: "https://zakupki.mos.ru/auction/" + it.auctionId,
    lots: [{ name: it.name || "Позиция", qty: "—", price }],
    matches: {}
  };
}

const raw = fetchRaw();
const purchases = (raw.items || []).map(map);
const payload = {
  generatedAt: new Date().toISOString(),
  source: "Портал поставщиков (zakupki.mos.ru) — активные котировочные сессии",
  total: raw.count,
  purchases
};
fs.mkdirSync(OUT_DIR, { recursive: true });
fs.writeFileSync(OUT, JSON.stringify(payload, null, 2), "utf8");
console.log(`wrote ${purchases.length} purchases (of ${raw.count} active) -> ${OUT}`);
