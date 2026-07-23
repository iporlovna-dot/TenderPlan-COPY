// Сборщик снапшота закупок для статичного фронта.
// Запускать НА МАШИНЕ, где доступен mos.ru (дата-центры гос-сайты блокируют!).
// Node 18+, использует системный curl. Пишет site/data/purchases.json.
//
// Тянет реестр активных КС, затем для КАЖДОЙ дозапрашивает карточку
// (Auction/Get) ради документов (files[]) и позиций (items[]).
//
//   node tools/build_snapshot.js
//   git add site/data/purchases.json && git commit -m "refresh snapshot" && git push
//   (затем на VPS: git pull — или попроси меня подтянуть)

const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const OUT_DIR = path.resolve(__dirname, "..", "site", "data");
const OUT = path.join(OUT_DIR, "purchases.json");

const LIST_API = "https://old.zakupki.mos.ru/api/Cssp/Purchase/Query";
const CARD_API = "https://zakupki.mos.ru/newapi/api/Auction/Get";
const FILE_API = "https://zakupki.mos.ru/newapi/api/FileStorage/Download";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36";
const TAKE = Number(process.env.LK_SNAPSHOT_TAKE || 48);

function curlJson(url, extraArgs = []) {
  const out = execFileSync("curl", [
    "-s", "-A", UA, "--max-time", "25", ...extraArgs, url
  ], { maxBuffer: 20 * 1024 * 1024, encoding: "utf8" });
  return JSON.parse(out);
}

const POOL = Number(process.env.LK_POOL || 400);  // сколько тянуть из реестра, чтобы отобрать открытые
function fetchList() {
  const queryDto = JSON.stringify({
    filter: { typeIn: { values: [1] }, auctionSpecificFilter: { stateIdIn: [19000002] } },
    order: [{ field: "endDate", desc: true }],  // самые поздние дедлайны сверху = среди них открытые
    withCount: true, take: POOL, skip: 0
  });
  return curlJson(LIST_API, ["-G", "--data-urlencode", "queryDto=" + queryDto]);
}

function fetchCard(auctionId) {
  try {
    return curlJson(CARD_API + "?auctionId=" + auctionId);
  } catch (e) {
    return null;
  }
}

const DAY = 86400000;
function parseListDate(s) {
  const m = /^(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2}):(\d{2})/.exec(s || "");
  return m ? new Date(+m[3], +m[2] - 1, +m[1], +m[4], +m[5], +m[6]) : null;
}
// "dd.MM.yyyy HH:mm:ss" -> "yyyy-MM-ddTHH:mm:ss" (naive local, для живого отсчёта на клиенте)
function toIso(s) {
  const m = /^(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2}):(\d{2})/.exec(s || "");
  return m ? `${m[3]}-${m[2]}-${m[1]}T${m[4]}:${m[5]}:${m[6]}` : null;
}

function map(it) {
  const now = new Date();
  const end = parseListDate(it.endDate), begin = parseListDate(it.beginDate);
  const cust = (it.customers && it.customers[0]) || {};
  const price = Number(it.startPrice) || 0;
  const aid = it.auctionId;

  // обогащение карточкой: документы + позиции
  let documents = [];
  let lots = [{ name: it.name || "Позиция", qty: "—", price }];
  let okpd = "";
  let deliveryDays = null;
  let deliveryPlace = "";
  const card = fetchCard(aid);
  if (card) {
    documents = (card.files || []).map(f => ({
      id: String(f.id),
      name: f.name || ("файл " + f.id),
      url: FILE_API + "?id=" + f.id
    }));
    if (Array.isArray(card.items) && card.items.length) {
      lots = card.items.map(i => ({
        name: i.name || "Позиция",
        qty: String(i.currentValue ?? "—"),
        price: Number(i.costPerUnit) || 0,
        okpd: i.okpdName || ""
      }));
      okpd = lots[0].okpd || "";
    }
    if (Array.isArray(card.deliveries) && card.deliveries.length) {
      const now = new Date();
      const days = card.deliveries.map(x => {
        if (typeof x.periodDaysTo === "number") return x.periodDaysTo;
        const dt = parseListDate(x.periodDateTo) || (x.periodDateTo ? new Date(x.periodDateTo) : null);
        return dt ? Math.max(0, Math.round((dt - now) / DAY)) : null;
      }).filter(x => typeof x === "number");
      if (days.length) deliveryDays = Math.max(...days);
      deliveryPlace = card.deliveries[0].deliveryPlace || "";
    }
  }

  return {
    id: "pp_" + aid,
    number: String(it.number || aid),
    title: it.name || "Котировочная сессия",
    customer: cust.name || "—",
    customerInn: cust.inn || "",
    law: it.federalLawName || "44-ФЗ",
    source: "Портал поставщиков (mos.ru)",
    region: (it.regionName || "").trim().replace(/^г\s+/, "г. "),
    okpd,
    price,
    stage: it.stateId === 19000002 ? "active" : (end && end > now ? "active" : "committee"),
    endDate: toIso(it.endDate),
    beginDate: toIso(it.beginDate),
    deadlineDays: end ? Math.max(0, Math.ceil((end - now) / DAY)) : 0,
    publishedDaysAgo: begin ? Math.max(0, Math.round((now - begin) / DAY)) : 0,
    guaranteeApp: 0, guaranteeContract: 0, prepayment: 0,
    href: "https://zakupki.mos.ru/auction/" + aid,
    deliveryDays,
    deliveryPlace,
    lots,
    documents,
    matches: {}
  };
}

const raw = fetchList();
const now = Date.now();
// берём только КС с реально открытой подачей (endDate в будущем), ближайшие к дедлайну сверху
const openItems = (raw.items || [])
  .filter(it => { const e = parseListDate(it.endDate); return e && e.getTime() > now; })
  .sort((a, b) => parseListDate(a.endDate) - parseListDate(b.endDate))
  .slice(0, TAKE);

const purchases = [];
let withDocs = 0;
openItems.forEach((it, i) => {
  const p = map(it);
  if (p.documents.length) withDocs++;
  purchases.push(p);
  process.stdout.write(`\r  карточек обработано: ${i + 1}/${openItems.length}`);
});
process.stdout.write("\n");

const payload = {
  generatedAt: new Date().toISOString(),
  source: "Портал поставщиков (zakupki.mos.ru) — активные котировочные сессии",
  total: raw.count,
  purchases
};
fs.mkdirSync(OUT_DIR, { recursive: true });
fs.writeFileSync(OUT, JSON.stringify(payload, null, 2), "utf8");
console.log(`wrote ${purchases.length} открытых КС (пул ${raw.items ? raw.items.length : 0}/${raw.count} в статусе «Активная»), ${withDocs} с документами -> ${OUT}`);
