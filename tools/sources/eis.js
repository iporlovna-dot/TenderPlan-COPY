// Адаптер ЕИС (zakupki.gov.ru). RSS расширенного поиска (44+223, «подача идёт»)
// + со страницы карточки: срок окончания подачи и документы (вкладка documents).
// Экспортирует async collectEis(limit). Конкурентный сбор карточек.

const { curlAsync, mapLimit } = require("./util");

const RSS = "https://zakupki.gov.ru/epz/order/extendedsearch/rss.html";
const DAY = 86400000;
const CONC = Number(process.env.LK_EIS_CONC || 5);

async function curlText(url, extraArgs = []) {
  return curlAsync(["-L", ...extraArgs, url]);
}

function decodeEntities(s) {
  return (s || "")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&");
}
function field(desc, label) {
  const re = new RegExp(label + ":\\s*</strong>\\s*([^<]+?)\\s*<", "i");
  const m = re.exec(desc);
  return m ? m[1].trim() : "";
}
function toIso(s) {
  const m = /(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{2}):(\d{2}))?/.exec(s || "");
  if (!m) return null;
  return `${m[3]}-${m[2]}-${m[1]}T${m[4] || "23"}:${m[5] || "59"}:00`;
}

async function fetchRssPage(page) {
  return curlText(RSS, [
    "-G", "--data", "fz44=on", "--data", "fz223=on", "--data", "af=on",
    "--data", "sortBy=UPDATE_DATE", "--data", "recordsPerPage=_50",
    "--data", "pageNumber=" + page,
  ]);
}

function parseItems(xml) {
  const items = [];
  for (const raw of xml.split("<item>").slice(1)) {
    const block = raw.split("</item>")[0];
    const link = (/<link>([^<]+)<\/link>/.exec(block) || [])[1] || "";
    const desc = decodeEntities((/<description>([\s\S]*?)<\/description>/.exec(block) || [])[1] || "");
    const number = (/regNumber=(\d+)/.exec(link) || [])[1];
    if (!number) continue;
    items.push({
      number, link,
      title: field(desc, "Наименование объекта закупки") || "Закупка",
      law: field(desc, "Размещение выполняется по") || "44-ФЗ",
      customer: field(desc, "Наименование Заказчика") || "—",
      price: parseFloat(field(desc, "Начальная цена контракта")) || 0,
      published: field(desc, "Размещено"),
    });
  }
  return items;
}

async function fetchCardInfo(link) {
  try {
    const html = await curlText(link);
    const dm = /Окончание подачи заявок<\/div>\s*<div class="data-block__value">\s*(\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2})?)/.exec(html);
    const endIso = dm ? toIso(dm[1]) : null;
    const hm = /href="([^"]*documents\.html[^"]*)"/i.exec(html);
    let docsUrl = hm ? hm[1] : null;
    if (docsUrl && docsUrl.startsWith("/")) docsUrl = "https://zakupki.gov.ru" + docsUrl;
    return { endIso, docsUrl };
  } catch (e) { return { endIso: null, docsUrl: null }; }
}

async function fetchDocs(docsUrl) {
  try {
    const html = await curlText(docsUrl);
    const seen = new Set(), docs = [];
    const re = /<a((?:[^>"']|"[^"]*"|'[^']*')*)>([\s\S]*?)<\/a>/gi;
    let m;
    while ((m = re.exec(html)) && docs.length < 20) {
      const attrs = m[1], inner = m[2];
      if (!/file\.html\?uid=/i.test(attrs)) continue;
      const url = (/(https?:\/\/[^"'\s]*filestore[^"'\s]*file\.html\?uid=[0-9A-Fa-f]+)/i.exec(attrs) || [])[1];
      if (!url) continue;
      const uid = (/uid=([0-9A-Fa-f]+)/i.exec(url) || [])[1];
      if (!uid || seen.has(uid)) continue;
      let name = inner.replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim();
      if (!name || name.length > 160) {
        const t = /title="([^"]*)"|title='([^']*)'/i.exec(attrs);
        name = (t && (t[1] || t[2])) ? (t[1] || t[2]).trim() : "документ " + uid.slice(0, 8);
      }
      seen.add(uid);
      docs.push({ id: uid, name, url });
    }
    return docs;
  } catch (e) { return []; }
}

async function mapItem(it) {
  const info = await fetchCardInfo(it.link);
  const documents = info.docsUrl ? await fetchDocs(info.docsUrl) : [];
  const now = Date.now();
  const end = info.endIso ? new Date(info.endIso).getTime() : null;
  const pub = toIso(it.published);
  return {
    id: "eis_" + it.number,
    number: it.number,
    title: it.title,
    customer: it.customer,
    customerInn: "",
    law: /223/.test(it.law) ? "223-ФЗ" : "44-ФЗ",
    source: "ЕИС (zakupki.gov.ru)",
    region: "", okpd: "", price: it.price, stage: "active",
    endDate: info.endIso,
    beginDate: pub,
    deadlineDays: end ? Math.max(0, Math.ceil((end - now) / DAY)) : 0,
    publishedDaysAgo: pub ? Math.max(0, Math.round((now - new Date(pub).getTime()) / DAY)) : 0,
    guaranteeApp: 0, guaranteeContract: 0, prepayment: 0,
    href: it.link,
    deliveryDays: null, deliveryPlace: "",
    lots: [{ name: it.title, qty: "—", price: it.price }],
    documents, matches: {},
  };
}

async function collectEis(limit = 75) {
  const seen = new Set();
  let items = [];
  const pages = Math.ceil(limit / 40) + 1;
  for (let page = 1; page <= pages && items.length < limit; page++) {
    let parsed;
    try { parsed = parseItems(await fetchRssPage(page)); } catch (e) { break; }
    if (!parsed.length) break;
    for (const it of parsed) {
      if (seen.has(it.number)) continue;
      seen.add(it.number); items.push(it);
    }
  }
  items = items.slice(0, limit);
  const out = await mapLimit(items, CONC, mapItem,
    (k, t) => process.stdout.write(`\r  ЕИС: ${k}/${t}`));
  if (items.length) process.stdout.write("\n");
  return out.filter(Boolean);
}

module.exports = { collectEis };
