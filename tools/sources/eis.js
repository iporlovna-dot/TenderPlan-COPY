// Адаптер ЕИС (zakupki.gov.ru). Массовый сбор через HTML расширенного поиска
// (results.html, 50 закупок/страница) — оттуда номер, закон, предмет, заказчик,
// цена и СРОК окончания подачи (без захода в карточку). Документы дозапрашиваем
// только для ближайших к дедлайну (docsLimit), чтобы не гонять тысячи запросов.
//
// Общий пул «последние обновлённые» — по сути случайная выборка по всем категориям
// сразу (закупок в ЕИС — десятки тысяч), поэтому узкие темы туда почти не попадают.
// ЕИС поддерживает полнотекстовый поиск (searchString+morphology=on) — прогоняем
// им ключевые слова (по умолчанию — медицинские) отдельными проходами и сливаем
// с общим пулом, чтобы такие темы гарантированно были в снапшоте.
//
// Экспортирует async collectEis(listLimit, docsLimit, keywords).

const { curlAsync, mapLimit } = require("./util");

const RESULTS = "https://zakupki.gov.ru/epz/order/extendedsearch/results.html";
const DAY = 86400000;
const CONC = Number(process.env.LK_EIS_CONC || 6);

const DEFAULT_MED_KEYWORDS = [
  "медицинские изделия", "перчатки медицинские", "шприцы", "лекарственные препараты",
  "медицинское оборудование", "стоматологические материалы", "хирургические инструменты",
  "лабораторные реагенты", "средства индивидуальной защиты медицинские", "ларингоскоп",
  "клинки для ларингоскопа",
];

async function curlText(url, extraArgs = []) {
  return curlAsync(["-L", ...extraArgs, url]);
}

function toIso(s) {
  const m = /(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{2}):(\d{2}))?/.exec(s || "");
  if (!m) return null;
  return `${m[3]}-${m[2]}-${m[1]}T${m[4] || "23"}:${m[5] || "59"}:00`;
}
function stripTags(s) { return (s || "").replace(/<[^>]+>/g, " ").replace(/&nbsp;/g, " ").replace(/\s+/g, " ").trim(); }
function parsePrice(s) {
  let t = stripTags(s).split("&#")[0].split("₽")[0]; // до символа валюты (&#8381; / ₽)
  t = t.replace(/[^\d,]/g, "").replace(",", ".");
  return parseFloat(t) || 0;
}

async function fetchResultsPage(page, searchString) {
  const args = [
    "-G", "--data", "fz44=on", "--data", "fz223=on", "--data", "af=on",
    "--data", "sortBy=UPDATE_DATE", "--data", "recordsPerPage=_50",
    "--data", "pageNumber=" + page,
  ];
  if (searchString) {
    args.push("--data-urlencode", "searchString=" + searchString, "--data", "morphology=on");
  }
  return curlText(RESULTS, args);
}

function parseHtmlItems(html) {
  const items = [];
  const blocks = html.split("search-registry-entry-block").slice(1);
  for (const b of blocks) {
    const number = (/regNumber=(\d+)/.exec(b) || [])[1];
    if (!number) continue;
    let link = (/href="([^"]*(?:common-info|order\/notice|purchase\/info)[^"]*regNumber=\d+[^"]*)"/i.exec(b) || [])[1]
      || (/href="([^"]*regNumber=\d+[^"]*)"/i.exec(b) || [])[1] || "";
    if (link.startsWith("/")) link = "https://zakupki.gov.ru" + link;
    const lawType = stripTags((/registry-entry__header-top__title[^>]*>([\s\S]*?)<\/div>/i.exec(b) || [])[1]);
    const title = stripTags((/registry-entry__body-value[^>]*>([\s\S]*?)<\/div>/i.exec(b) || [])[1]) || "Закупка";
    const price = parsePrice((/price-block__value[^>]*>([\s\S]*?)<\/div>/i.exec(b) || [])[1]);
    const endRaw = (/Окончание подачи заявок[\s\S]*?data-block__value[^>]*>([\s\S]*?)<\/div>/i.exec(b) || [])[1];
    const pubRaw = (/Размещено[\s\S]*?data-block__value[^>]*>([\s\S]*?)<\/div>/i.exec(b) || [])[1];
    const customer = stripTags((/registry-entry__body-href[^>]*>[\s\S]*?<a[^>]*>([\s\S]*?)<\/a>/i.exec(b) || [])[1]) || "—";
    items.push({
      number, link, title, customer, price,
      law: /223/.test(lawType) ? "223-ФЗ" : "44-ФЗ",
      endIso: toIso(stripTags(endRaw)),
      pubIso: toIso(stripTags(pubRaw)),
    });
  }
  return items;
}

const REGION_LOWER_WORDS = new Set(["область", "край", "округ", "автономный", "автономная", "автономного", "район", "района"]);

// Нормализуем субъект РФ под вид Портала: «МАГАДАНСКАЯ ОБЛАСТЬ» → «Магаданская область».
function tidyRegion(s) {
  s = (s || "").replace(/\s+/g, " ").trim();
  if (!s) return "";
  if (!/[а-яё]/.test(s)) s = s.toLowerCase();          // ALL CAPS → нижний регистр
  s = s.replace(/(^|[\s-])([а-яёa-z])/g, (_, p, c) => p + c.toUpperCase());
  // \b не видит кириллицу как «словесные» символы в JS-регэкспах, поэтому разбираем
  // по словам-токенам, а не по границе слова.
  return s.split(" ").map(w => REGION_LOWER_WORDS.has(w.toLowerCase()) ? w.toLowerCase() : w).join(" ");
}
// Регион берём из адреса «Место нахождения» в карточке (в выдаче его нет).
function extractRegion(html) {
  const m = /Место нахождения<\/(?:div|span)>\s*<(?:div|span)[^>]*>([\s\S]*?)<\/(?:div|span)>/i.exec(html || "");
  if (!m) return "";
  const parts = stripTags(m[1]).split(",").map(x => x.replace(/\s+/g, " ").trim()).filter(Boolean);
  for (const p of parts) {
    const c = /^(?:г\.?\s*|город\s+)?(москва|санкт[- ]петербург|севастополь)$/i.exec(p);
    if (c) return "г. " + tidyRegion(c[1]);
  }
  const subj = parts.find(x => /(облас|край|республик|автономн|округ|кузбасс)/i.test(x));
  return subj ? tidyRegion(subj) : "";
}

// со страницы карточки: ссылка на вкладку документов + регион (один заход)
async function fetchCardMeta(link) {
  try {
    const html = await curlText(link);
    let u = (/href="([^"]*documents\.html[^"]*)"/i.exec(html) || [])[1];
    if (u && u.startsWith("/")) u = "https://zakupki.gov.ru" + u;
    return { docsUrl: u || null, region: extractRegion(html) };
  } catch (e) { return { docsUrl: null, region: "" }; }
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

function toPurchase(it, documents, region) {
  const now = Date.now();
  const end = it.endIso ? new Date(it.endIso).getTime() : null;
  return {
    id: "eis_" + it.number,
    number: it.number,
    title: it.title,
    customer: it.customer,
    customerInn: "",
    law: it.law,
    source: "ЕИС (zakupki.gov.ru)",
    region: region || "", okpd: "", price: it.price, stage: "active",
    endDate: it.endIso,
    beginDate: it.pubIso,
    deadlineDays: end ? Math.max(0, Math.ceil((end - now) / DAY)) : 0,
    publishedDaysAgo: it.pubIso ? Math.max(0, Math.round((now - new Date(it.pubIso).getTime()) / DAY)) : 0,
    guaranteeApp: 0, guaranteeContract: 0, prepayment: 0,
    href: it.link,
    deliveryDays: null, deliveryPlace: "",
    lots: [{ name: it.title, qty: "—", price: it.price }],
    documents: documents || [],
    matches: {},
  };
}

// Прогоняет постраничный сбор results.html (опционально с ключевым словом) в
// общий Set/массив items, пока не наберёт take штук или страницы не кончатся.
async function fetchPool(seen, items, take, searchString, label) {
  const maxPages = Math.ceil(take / 50) + 1;
  let got = 0;
  for (let page = 1; page <= maxPages && got < take; page++) {
    let parsed;
    try { parsed = parseHtmlItems(await fetchResultsPage(page, searchString)); } catch (e) { break; }
    if (!parsed.length) break;
    for (const it of parsed) {
      if (seen.has(it.number)) continue;
      seen.add(it.number); items.push(it); got++;
    }
    process.stdout.write(`\r  ЕИС ${label}: ${got}/${take}`);
  }
  process.stdout.write("\n");
}

async function collectEis(listLimit = 600, docsLimit = 150, keywords = DEFAULT_MED_KEYWORDS) {
  const seen = new Set();
  let items = [];

  // 1) прицельные проходы по ключевым словам — иначе узкие темы (медицина и т.п.)
  // почти не попадают в общий пул «последние обновлённые по всем категориям»
  const perKeyword = Number(process.env.LK_EIS_KEYWORD_TAKE || 60);
  for (const kw of keywords) {
    await fetchPool(seen, items, perKeyword, kw, `«${kw}»`);
  }

  // 2) общий список — для широты охвата остальных тем
  await fetchPool(seen, items, listLimit, undefined, "список");

  // только с будущим сроком, ближайшие сверху
  const now = Date.now();
  items = items
    .filter(it => it.endIso && new Date(it.endIso).getTime() > now)
    .sort((a, b) => new Date(a.endIso) - new Date(b.endIso));

  // 3) документы + регион — только для ближайших docsLimit (один заход в карточку)
  const withDocs = items.slice(0, docsLimit);
  const metaById = {};
  await mapLimit(withDocs, CONC, async (it) => {
    const meta = await fetchCardMeta(it.link);
    metaById[it.number] = {
      docs: meta.docsUrl ? await fetchDocs(meta.docsUrl) : [],
      region: meta.region || "",
    };
  }, (k, t) => process.stdout.write(`\r  ЕИС документы: ${k}/${t}`));
  if (withDocs.length) process.stdout.write("\n");

  return items.map(it => {
    const m = metaById[it.number] || {};
    return toPurchase(it, m.docs || [], m.region || "");
  });
}

module.exports = { collectEis };
