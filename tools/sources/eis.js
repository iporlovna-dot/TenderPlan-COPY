// Адаптер ЕИС (zakupki.gov.ru) для сборщика снапшота.
// Источник — RSS расширенного поиска (чистый XML, все поля кроме срока — оттуда),
// срок окончания подачи дозапрашиваем со страницы карточки (следуя редиректу).
// Экспортирует collectEis(limit). Запуск напрямую пишет только ЕИС (для отладки).

const { execFileSync } = require("child_process");

const RSS = "https://zakupki.gov.ru/epz/order/extendedsearch/rss.html";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36";
const DAY = 86400000;

function curlText(url, extraArgs = []) {
  return execFileSync("curl", ["-s", "-L", "-A", UA, "--max-time", "30", ...extraArgs, url],
    { maxBuffer: 30 * 1024 * 1024, encoding: "utf8" });
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

// "dd.MM.yyyy" | "dd.MM.yyyy HH:mm" -> ISO "yyyy-MM-ddTHH:mm:ss"
function toIso(s) {
  const m = /(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{2}):(\d{2}))?/.exec(s || "");
  if (!m) return null;
  return `${m[3]}-${m[2]}-${m[1]}T${m[4] || "23"}:${m[5] || "59"}:00`;
}

function fetchRssPage(page) {
  return curlText(RSS, [
    "-G",
    "--data", "fz44=on", "--data", "fz223=on", "--data", "af=on",
    "--data", "sortBy=UPDATE_DATE", "--data", "recordsPerPage=_50",
    "--data", "pageNumber=" + page,
  ]);
}

function parseItems(xml) {
  const items = [];
  const chunks = xml.split("<item>").slice(1);
  for (const raw of chunks) {
    const block = raw.split("</item>")[0];
    const link = (/<link>([^<]+)<\/link>/.exec(block) || [])[1] || "";
    const desc = decodeEntities((/<description>([\s\S]*?)<\/description>/.exec(block) || [])[1] || "");
    const number = (/regNumber=(\d+)/.exec(link) || [])[1];
    if (!number) continue;
    items.push({
      number,
      link,
      title: field(desc, "Наименование объекта закупки") || "Закупка",
      law: field(desc, "Размещение выполняется по") || "44-ФЗ",
      customer: field(desc, "Наименование Заказчика") || "—",
      price: parseFloat(field(desc, "Начальная цена контракта")) || 0,
      published: field(desc, "Размещено"),
      stageName: field(desc, "Этап размещения"),
    });
  }
  return items;
}

// срок окончания подачи со страницы карточки
function fetchDeadlineIso(link) {
  try {
    const html = curlText(link);
    const m = /Окончание подачи заявок<\/div>\s*<div class="data-block__value">\s*(\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2})?)/.exec(html);
    return m ? toIso(m[1]) : null;
  } catch (e) {
    return null;
  }
}

function collectEis(limit = 24) {
  const seen = new Set();
  let items = [];
  for (let page = 1; page <= 4 && items.length < limit * 2; page++) {
    try {
      const pageItems = parseItems(fetchRssPage(page));
      for (const it of pageItems) {
        if (seen.has(it.number)) continue;
        seen.add(it.number);
        items.push(it);
      }
      if (pageItems.length === 0) break;
    } catch (e) { break; }
  }
  items = items.slice(0, limit);

  const now = Date.now();
  const purchases = [];
  items.forEach((it, i) => {
    const endIso = fetchDeadlineIso(it.link);
    const end = endIso ? new Date(endIso).getTime() : null;
    const pub = toIso(it.published);
    purchases.push({
      id: "eis_" + it.number,
      number: it.number,
      title: it.title,
      customer: it.customer,
      customerInn: "",
      law: /223/.test(it.law) ? "223-ФЗ" : "44-ФЗ",
      source: "ЕИС (zakupki.gov.ru)",
      region: "",
      okpd: "",
      price: it.price,
      stage: "active",
      endDate: endIso,
      beginDate: pub,
      deadlineDays: end ? Math.max(0, Math.ceil((end - now) / DAY)) : 0,
      publishedDaysAgo: pub ? Math.max(0, Math.round((now - new Date(pub).getTime()) / DAY)) : 0,
      guaranteeApp: 0, guaranteeContract: 0, prepayment: 0,
      href: it.link,
      deliveryDays: null,
      deliveryPlace: "",
      lots: [{ name: it.title, qty: "—", price: it.price }],
      documents: [],
      matches: {},
    });
    process.stdout.write(`\r  ЕИС: ${i + 1}/${items.length}`);
  });
  if (items.length) process.stdout.write("\n");
  return purchases;
}

module.exports = { collectEis };

if (require.main === module) {
  const list = collectEis(Number(process.env.LK_EIS_TAKE || 10));
  console.log(`ЕИС: ${list.length} закупок, со сроком: ${list.filter(p => p.endDate).length}`);
  console.log(list.slice(0, 5).map(p => `${p.law} · ${p.price}₽ · ${p.title.slice(0, 40)} · до ${p.endDate || "?"}`).join("\n"));
}
