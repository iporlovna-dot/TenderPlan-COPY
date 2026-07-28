// Адаптер реестра КОНТРАКТОВ ЕИС (zakupki.gov.ru/epz/contract/search/results.html) —
// это отдельная база от активных закупок: заключённые (в т.ч. исполненные) контракты
// с их реальной ценой. Источник аналитики (ценовой ориентир по категориям, история
// заказчика), а не ленты закупок.
//
// В отличие от поиска по активным закупкам (order/extendedsearch) этот эндпоинт
// НЕ глушит запросы с searchString для голых HTTP-клиентов — проверено вручную:
// заведомо-бессмысленное слово даёт честные 0 записей, обычные слова — реальные
// результаты, всё через curl без Chromium (см. CLAUDE.md/plan.md).
//
// Список видимых полей у контракта: заказчик (текст, БЕЗ ИНН — ссылка на карточку
// заказчика в списке всегда с пустым inn=), предмет закупки (краткий, с подсветкой
// найденного слова), цена контракта (реальная, не НМЦК), дата заключения. Имени
// поставщика-победителя в списке нет — только в карточке конкретного контракта
// (отдельный заход, здесь сознательно не делаем ради скорости — агрегаты считаем
// без него).

const { curlAsync, mapLimit, sleep } = require("./util");

const RESULTS = "https://zakupki.gov.ru/epz/contract/search/results.html";
const CONC = Number(process.env.LK_CONTRACTS_CONC || 5);

function stripTags(s) {
  return (s || "")
    .replace(/<\/?span[^>]*>/gi, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
}
function parsePrice(s) {
  let t = stripTags(s).split("&#")[0].split("₽")[0];
  t = t.replace(/[^\d,]/g, "").replace(",", ".");
  return parseFloat(t) || 0;
}
function toIso(s) {
  const m = /(\d{2})\.(\d{2})\.(\d{4})/.exec(s || "");
  if (!m) return null;
  return `${m[3]}-${m[2]}-${m[1]}`;
}

async function fetchContractsPage(page, searchString) {
  // Без -L: с follow-redirect этот эндпоинт почему-то теряет searchString и
  // молча отдаёт 0 записей (проверено вручную) — прямой URL без редиректов
  // работает надёжно, как в браузере.
  const params = new URLSearchParams({ recordsPerPage: "_50", pageNumber: String(page) });
  if (searchString) params.set("searchString", searchString);
  return curlAsync([RESULTS + "?" + params.toString()]);
}

function parseContractItems(html) {
  const items = [];
  const blocks = html.split("search-registry-entry-block").slice(1);
  for (const b of blocks) {
    const reestrNumber = (/reestrNumber=(\d+)/.exec(b) || [])[1];
    if (!reestrNumber) continue;
    const customer = stripTags(
      (/registry-entry__body-title[^>]*>Заказчик<\/div>\s*<div class="registry-entry__body-href">\s*<a[^>]*>([\s\S]*?)<\/a>/i.exec(b) || [])[1]
    );
    const itemText = stripTags(
      (/lots-wrap-content__body__val"[^>]*>\s*<span class="pl-0 col">([\s\S]*?)<a target="_blank"/i.exec(b) || [])[1]
      || (/lots-wrap-content__body__val"[^>]*>\s*<span class="pl-0 col">([\s\S]*?)<\/span>\s*<\/div>/i.exec(b) || [])[1]
    );
    const price = parsePrice((/price-block__value[^>]*>([\s\S]*?)<\/div>/i.exec(b) || [])[1]);
    const dateRaw = (/Заключение контракта<\/div>\s*<div class="data-block__value">([\s\S]*?)<\/div>/i.exec(b) || [])[1];
    if (!customer || !price) continue;
    items.push({ reestrNumber, customer, item: itemText || "", price, date: toIso(dateRaw) });
  }
  return items;
}

// Собирает контракты по одному ключевому слову (до take штук, по 50/страница).
async function fetchContractsForKeyword(keyword, take) {
  const maxPages = Math.ceil(take / 50) + 1;
  const out = [];
  const seen = new Set();
  for (let page = 1; page <= maxPages && out.length < take; page++) {
    let parsed;
    try {
      parsed = parseContractItems(await fetchContractsPage(page, keyword));
    } catch (e) { break; }
    if (!parsed.length) break;
    for (const it of parsed) {
      if (seen.has(it.reestrNumber)) continue;
      seen.add(it.reestrNumber); out.push(it);
    }
  }
  return out.slice(0, take);
}

// Прогоняет весь список тем, для каждой — до perKeywordTake контрактов.
// Возвращает { [keyword]: ContractItem[] }.
async function collectContractsByKeyword(keywords, perKeywordTake = 80) {
  const result = {};
  let done = 0;
  await mapLimit(keywords, CONC, async (kw) => {
    result[kw] = await fetchContractsForKeyword(kw, perKeywordTake);
    done++;
    process.stdout.write(`\r  Реестр контрактов: ${done}/${keywords.length} тем`);
  });
  process.stdout.write("\n");
  return result;
}

module.exports = { collectContractsByKeyword, fetchContractsForKeyword };
