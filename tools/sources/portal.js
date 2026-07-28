// Адаптер «Портал поставщиков» (zakupki.mos.ru). Реестр активных КС → открытые →
// карточка каждой (документы, позиции, срок поставки), с конкурентностью.
// Экспортирует async collectPortal(take).

const { curlAsync, mapLimit } = require("./util");

const LIST_API = "https://old.zakupki.mos.ru/api/Cssp/Purchase/Query";
const CARD_API = "https://zakupki.mos.ru/newapi/api/Auction/Get";
const FILE_API = "https://zakupki.mos.ru/newapi/api/FileStorage/Download";
const DAY = 86400000;
const CONC = Number(process.env.LK_PORTAL_CONC || 8);

async function curlJson(url, extraArgs = []) {
  return JSON.parse(await curlAsync([...extraArgs, url]));
}
async function fetchList(pool) {
  const queryDto = JSON.stringify({
    filter: { typeIn: { values: [1] }, auctionSpecificFilter: { stateIdIn: [19000002] } },
    order: [{ field: "endDate", desc: true }],
    withCount: true, take: pool, skip: 0,
  });
  return curlJson(LIST_API, ["-G", "--data-urlencode", "queryDto=" + queryDto]);
}
async function fetchCard(auctionId) {
  try { return await curlJson(CARD_API + "?auctionId=" + auctionId); } catch (e) { return null; }
}

function parseListDate(s) {
  const m = /^(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2}):(\d{2})/.exec(s || "");
  return m ? new Date(+m[3], +m[2] - 1, +m[1], +m[4], +m[5], +m[6]) : null;
}
function toIso(s) {
  const m = /^(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2}):(\d{2})/.exec(s || "");
  return m ? `${m[3]}-${m[2]}-${m[1]}T${m[4]}:${m[5]}:${m[6]}` : null;
}

async function mapItem(it) {
  const now = new Date();
  const end = parseListDate(it.endDate), begin = parseListDate(it.beginDate);
  const cust = (it.customers && it.customers[0]) || {};
  const price = Number(it.startPrice) || 0;
  const aid = it.auctionId;

  let documents = [];
  let lots = [{ name: it.name || "Позиция", qty: "—", unit: "", price }];
  let okpd = "";
  let deliveryDays = null;
  let deliveryPlace = "";
  const card = await fetchCard(aid);
  if (card) {
    documents = (card.files || []).map(f => ({
      id: String(f.id), name: f.name || ("файл " + f.id), url: FILE_API + "?id=" + f.id,
    }));
    if (Array.isArray(card.items) && card.items.length) {
      lots = card.items.map(i => ({
        name: i.name || "Позиция", qty: String(i.currentValue ?? "—"),
        unit: i.okeiName || "",           // единица измерения (шт/упак/чел…) из ОКЕИ
        price: Number(i.costPerUnit) || 0, // costPerUnit = цена за ЕДИНИЦУ, не сумма
        okpd: i.okpdName || "",
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
    okpd, price, stage: "active",
    endDate: toIso(it.endDate),
    beginDate: toIso(it.beginDate),
    deadlineDays: end ? Math.max(0, Math.ceil((end - now) / DAY)) : 0,
    publishedDaysAgo: begin ? Math.max(0, Math.round((now - begin) / DAY)) : 0,
    guaranteeApp: 0, guaranteeContract: 0, prepayment: 0,
    href: "https://zakupki.mos.ru/auction/" + aid,
    deliveryDays, deliveryPlace, lots, documents, matches: {},
  };
}

async function collectPortal(take = 150) {
  const pool = Number(process.env.LK_POOL || 600);
  const raw = await fetchList(pool);
  const now = Date.now();
  const openItems = (raw.items || [])
    .filter(it => { const e = parseListDate(it.endDate); return e && e.getTime() > now; })
    .sort((a, b) => parseListDate(a.endDate) - parseListDate(b.endDate))
    .slice(0, take);

  const out = await mapLimit(openItems, CONC, mapItem,
    (k, t) => process.stdout.write(`\r  Портал: ${k}/${t}`));
  if (openItems.length) process.stdout.write("\n");
  return out.filter(Boolean);
}

module.exports = { collectPortal };
