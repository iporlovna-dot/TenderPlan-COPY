// Адаптер «Портал поставщиков» (zakupki.mos.ru) для сборщика снапшота.
// Тянет реестр активных КС, отбирает реально открытые (endDate в будущем),
// для каждой дозапрашивает карточку (документы, позиции, срок поставки).
// Экспортирует collectPortal(take).

const { execFileSync } = require("child_process");

const LIST_API = "https://old.zakupki.mos.ru/api/Cssp/Purchase/Query";
const CARD_API = "https://zakupki.mos.ru/newapi/api/Auction/Get";
const FILE_API = "https://zakupki.mos.ru/newapi/api/FileStorage/Download";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36";
const DAY = 86400000;

function curlJson(url, extraArgs = []) {
  const out = execFileSync("curl", ["-s", "-A", UA, "--max-time", "25", ...extraArgs, url],
    { maxBuffer: 20 * 1024 * 1024, encoding: "utf8" });
  return JSON.parse(out);
}

function fetchList(pool) {
  const queryDto = JSON.stringify({
    filter: { typeIn: { values: [1] }, auctionSpecificFilter: { stateIdIn: [19000002] } },
    order: [{ field: "endDate", desc: true }],
    withCount: true, take: pool, skip: 0,
  });
  return curlJson(LIST_API, ["-G", "--data-urlencode", "queryDto=" + queryDto]);
}
function fetchCard(auctionId) {
  try { return curlJson(CARD_API + "?auctionId=" + auctionId); } catch (e) { return null; }
}

function parseListDate(s) {
  const m = /^(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2}):(\d{2})/.exec(s || "");
  return m ? new Date(+m[3], +m[2] - 1, +m[1], +m[4], +m[5], +m[6]) : null;
}
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

  let documents = [];
  let lots = [{ name: it.name || "Позиция", qty: "—", price }];
  let okpd = "";
  let deliveryDays = null;
  let deliveryPlace = "";
  const card = fetchCard(aid);
  if (card) {
    documents = (card.files || []).map(f => ({
      id: String(f.id), name: f.name || ("файл " + f.id), url: FILE_API + "?id=" + f.id,
    }));
    if (Array.isArray(card.items) && card.items.length) {
      lots = card.items.map(i => ({
        name: i.name || "Позиция", qty: String(i.currentValue ?? "—"),
        price: Number(i.costPerUnit) || 0, okpd: i.okpdName || "",
      }));
      okpd = lots[0].okpd || "";
    }
    if (Array.isArray(card.deliveries) && card.deliveries.length) {
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
    stage: "active",
    endDate: toIso(it.endDate),
    beginDate: toIso(it.beginDate),
    deadlineDays: end ? Math.max(0, Math.ceil((end - now) / DAY)) : 0,
    publishedDaysAgo: begin ? Math.max(0, Math.round((now - begin) / DAY)) : 0,
    guaranteeApp: 0, guaranteeContract: 0, prepayment: 0,
    href: "https://zakupki.mos.ru/auction/" + aid,
    deliveryDays, deliveryPlace, lots, documents, matches: {},
  };
}

function collectPortal(take = 48) {
  const pool = Number(process.env.LK_POOL || 400);
  const raw = fetchList(pool);
  const now = Date.now();
  const openItems = (raw.items || [])
    .filter(it => { const e = parseListDate(it.endDate); return e && e.getTime() > now; })
    .sort((a, b) => parseListDate(a.endDate) - parseListDate(b.endDate))
    .slice(0, take);

  const purchases = [];
  openItems.forEach((it, i) => {
    purchases.push(map(it));
    process.stdout.write(`\r  Портал: ${i + 1}/${openItems.length}`);
  });
  if (openItems.length) process.stdout.write("\n");
  return purchases;
}

module.exports = { collectPortal };
