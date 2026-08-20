// Адаптер ЕИС (zakupki.gov.ru). Массовый сбор через HTML расширенного поиска
// (results.html, 50 закупок/страница) — оттуда номер, закон, предмет, заказчик,
// цена и СРОК окончания подачи (без захода в карточку). Документы дозапрашиваем
// только для ближайших к дедлайну (docsLimit), чтобы не гонять тысячи запросов.
//
// Общий пул «последние обновлённые» — по сути случайная выборка по всем категориям
// сразу (закупок в ЕИС — десятки тысяч), поэтому узкие темы туда почти не попадают.
// ЕИС поддерживает полнотекстовый поиск (searchString+morphology=on) — прогоняем
// им ключевые слова по основным категориям отдельными проходами и сливаем с общим
// пулом, чтобы такие темы гарантированно были в снапшоте (и находились точным
// поиском на сайте, а не только случайно).
//
// Общий список ограничен потолком выдачи в 5000 записей на запрос, поэтому вглубь
// корпуса ходим дата-срезами (день публикации × закон) — см. eissweep.js.
//
// Экспортирует async collectEis(listLimit, docsLimit, keywords, docsCache, regionIndex, sweep).

const { curlAsync, mapLimit, sleep } = require("./util");
const { regionFromNumber } = require("./eisregion");
const {
  enumerateSlices, pickSlices, advance, pruneSweep, sweepStats, SLICE_PAGES, SWEEP_TAKE,
} = require("./eissweep");

const RESULTS = "https://zakupki.gov.ru/epz/order/extendedsearch/results.html";
const DAY = 86400000;
const CONC = Number(process.env.LK_EIS_CONC || 10);   // замер: троттлинга нет и при 20
// сколько кэш документов считается свежим: ТЗ закупок правят, но не ежечасно —
// перепроверяем остатком бюджета, не отнимая его у ещё не покрытых закупок
const DOCS_TTL_MS = Number(process.env.LK_EIS_DOCS_TTL_DAYS || 7) * 86400000;

const DEFAULT_KEYWORDS = [
  // медицина
  "медицинские изделия", "перчатки медицинские", "шприцы", "лекарственные препараты",
  "медицинское оборудование", "стоматологические материалы", "хирургические инструменты",
  "лабораторные реагенты", "средства индивидуальной защиты медицинские", "ларингоскоп",
  "клинки для ларингоскопа",
  // общие/хозяйственные
  "перчатки", "спецодежда", "канцелярские товары", "хозяйственные товары", "бытовая химия",
  // стройка и ремонт
  "строительные материалы", "ремонтные работы", "электротовары", "сантехническое оборудование",
  // техника и IT
  "компьютерная техника", "оргтехника", "программное обеспечение",
  // услуги
  "охранные услуги", "уборка помещений", "транспортные услуги", "продукты питания",
  "мебель офисная", "автозапчасти", "пожарная безопасность",
];

async function curlText(url, extraArgs = []) {
  return curlAsync(["-L", ...extraArgs, url]);
}

function toIso(s) {
  const m = /(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{2}):(\d{2}))?/.exec(s || "");
  if (!m) return null;
  return `${m[3]}-${m[2]}-${m[1]}T${m[4] || "23"}:${m[5] || "59"}:00`;
}
function stripTags(s) {
  // подсветка найденного слова в поиске рвёт его пополам: «Поставка
  // <span class="highlightColor">ларингоскоп</span>ов» — span убираем без
  // пробела (это внутри слова), остальные теги — с пробелом (граница строк).
  return (s || "")
    .replace(/<\/?span[^>]*>/gi, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&")  // последним — иначе «&amp;lt;» распакуется в «<» дважды
    .replace(/\s+/g, " ")
    .trim();
}
function parsePrice(s) {
  let t = stripTags(s).split("&#")[0].split("₽")[0]; // до символа валюты (&#8381; / ₽)
  t = t.replace(/[^\d,]/g, "").replace(",", ".");
  return parseFloat(t) || 0;
}

// slice (необязателен) — дата-срез: { law: 44|223, day: "дд.мм.гггг" }. Сужает
// выдачу до одного дня публикации и одного закона, чтобы обойти потолок в 5000
// записей на запрос (см. eissweep.js). Внутри среза сортируем по дате публикации,
// а не обновления: обновления идут постоянно и перетасовывали бы страницы между
// последовательными запросами — часть записей мы бы прочли дважды, часть не
// увидели вовсе. Замер: одна и та же страница дважды подряд — совпало 50/50.
async function fetchResultsPage(page, searchString, slice = null) {
  const args = [
    "-G", "--data", "af=on", "--data", "recordsPerPage=_50",
    "--data", "pageNumber=" + page,
    "--data", "sortBy=" + (slice ? "PUBLISH_DATE" : "UPDATE_DATE"),
  ];
  if (slice && slice.law) args.push("--data", `fz${slice.law}=on`);
  else args.push("--data", "fz44=on", "--data", "fz223=on");
  if (slice && slice.day) {
    args.push("--data", "publishDateFrom=" + slice.day, "--data", "publishDateTo=" + slice.day);
  }
  if (searchString) {
    args.push("--data-urlencode", "searchString=" + searchString, "--data", "morphology=on");
  }
  return curlText(RESULTS, args);
}

// Тот же results.html, но через настоящий Chromium (Playwright), а не curl.
// Источник глушит поиск (searchString) для голых HTTP-клиентов — тот же самый
// запрос из настоящего браузера проходит нормально (проверено вручную: curl —
// «0 записей», Chromium с той же машины в тот же момент — реальные результаты).
// Общий список (без searchString) источник не глушит — там браузер не нужен,
// оставляем на curl (быстрее, без веса Chromium).
function resultsUrl(pageNum, searchString) {
  const params = new URLSearchParams({
    fz44: "on", fz223: "on", af: "on", sortBy: "UPDATE_DATE",
    recordsPerPage: "_50", pageNumber: String(pageNum),
  });
  if (searchString) { params.set("searchString", searchString); params.set("morphology", "on"); }
  return RESULTS + "?" + params.toString();
}
async function fetchResultsPageViaBrowser(browserPage, pageNum, searchString) {
  await browserPage.goto(resultsUrl(pageNum, searchString), { waitUntil: "networkidle", timeout: 30000 });
  return browserPage.content();
}

let _browserPromise = null;
async function getBrowser() {
  if (!_browserPromise) {
    // `playwright` тянет собственный chromium (~150 МБ) при установке. Там, где
    // его нет, хватает `playwright-core` + уже стоящего в системе Chrome: движку
    // важен настоящий браузерный отпечаток (см. CLAUDE.md, «Грабли» §8), а чей
    // это бинарник — неважно. Так сборщик поднимается на второй машине без
    // выкачивания браузера.
    let chromium;
    try { ({ chromium } = require("playwright")); }
    catch (e) { ({ chromium } = require("playwright-core")); }
    const opts = { headless: true };
    if (process.env.LK_CHROME_PATH) opts.executablePath = process.env.LK_CHROME_PATH;
    else if (process.env.LK_CHROME_CHANNEL) opts.channel = process.env.LK_CHROME_CHANNEL;
    _browserPromise = chromium.launch(opts);
  }
  return _browserPromise;
}
async function closeBrowser() {
  if (!_browserPromise) return;
  try { await (await _browserPromise).close(); } catch (e) { /* уже закрыт/не поднялся */ }
  _browserPromise = null;
}

function parseHtmlItems(html) {
  const items = [];
  const blocks = html.split("search-registry-entry-block").slice(1);
  for (const b of blocks) {
    const number = (/regNumber=(\d+)/.exec(b) || [])[1];
    if (!number) continue;
    // Ссылка на карточку закупки. И у 44-ФЗ (…/notice/<процедура>/view/common-info.html,
    // процедура разная: ea20/zk20/ezt20…), и у 223-ФЗ (…/purchase/info/common-info.html)
    // правильная всегда содержит common-info.html — на неё и якоримся. Иначе цеплялась
    // первая по порядку ссылка «printForm/listModal.html» (модалка печатной формы,
    // НЕ карточка закупки) — отсюда «кнопка открывает не ту страницу».
    let link = (/href="([^"]*common-info\.html\?regNumber=\d+[^"]*)"/i.exec(b) || [])[1]
      || (/href="([^"]*(?:\/view\/|purchase\/info)[^"]*regNumber=\d+[^"]*)"/i.exec(b) || [])[1]
      || (/href="([^"]*regNumber=\d+[^"]*)"/i.exec(b) || [])[1] || "";
    if (link.startsWith("/")) link = "https://zakupki.gov.ru" + link;
    const lawType = stripTags((/registry-entry__header-top__title[^>]*>([\s\S]*?)<\/div>/i.exec(b) || [])[1]);
    const title = stripTags((/registry-entry__body-value[^>]*>([\s\S]*?)<\/div>/i.exec(b) || [])[1]) || "Закупка";
    const price = parsePrice((/price-block__value[^>]*>([\s\S]*?)<\/div>/i.exec(b) || [])[1]);
    const endRaw = (/Окончание подачи заявок[\s\S]*?data-block__value[^>]*>([\s\S]*?)<\/div>/i.exec(b) || [])[1];
    const pubRaw = (/Размещено[\s\S]*?data-block__value[^>]*>([\s\S]*?)<\/div>/i.exec(b) || [])[1];
    const customer = stripTags((/registry-entry__body-href[^>]*>[\s\S]*?<a[^>]*>([\s\S]*?)<\/a>/i.exec(b) || [])[1]) || "—";
    // Способ закупки — из шапки блока (header-top__title). Неконкурентные 223-ФЗ
    // («Иной способ», единственный поставщик) — почти половина ленты 223: у них
    // «Окончание подачи заявок» стоит формальной датой в будущем, но реальных
    // торгов нет — договор часто заключён сразу, на ЕИС «завершена». Помечаем
    // competitive=false, чтобы фронт не крутил у них ложный отсчёт «до конца подачи».
    const procedureType = lawType;
    const competitive = !/иной способ|единственн/i.test(procedureType);
    // Реальный этап процедуры («Подача заявок» / «Работа комиссии» / «Определение
    // поставщика завершено» / …) — тот же заголовок, что и в шапке карточки закупки,
    // только здесь бесплатно: уже лежит в блоке списка, отдельный заход не нужен.
    // Без него «до конца подачи» врёт для закупок, которые уже ушли в работу
    // комиссии раньше формальной даты (нашли на примере 32616298437 — «Приобретение
    // GPS оборудования», подача формально ещё N часов, а по факту уже «Работа
    // комиссии», подаваться поздно).
    const procStage = stripTags((/header-mid__title[^>]*>([\s\S]*?)<\/div>/i.exec(b) || [])[1]) || "";
    items.push({
      number, link, title, customer, price,
      law: /223/.test(lawType) ? "223-ФЗ" : "44-ФЗ",
      procedureType, competitive, procStage,
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

// Значение поля карточки по заголовку: на common-info поля идут парами
// section__title → section__info. Возвращаем текст первого section__info, чей
// заголовок подошёл под titleRe (пустая строка, если не нашли).
function cardField(html, titleRe) {
  const re = /section__title[^>]*>([\s\S]*?)<\/span>[\s\S]*?section__info[^>]*>([\s\S]*?)<\/span>/gi;
  let m;
  while ((m = re.exec(html))) {
    if (titleRe.test(stripTags(m[1]))) return stripTags(m[2]);
  }
  return "";
}

// Срок поставки из карточки → в днях. Берём ТОЛЬКО когда поле «Срок исполнения
// контракта» задано явной длительностью — «N (рабочих|календарных) дней»: это и есть
// реальный срок исполнения/поставки. Форму-ДАТУ «дд.мм.гггг» сознательно НЕ берём —
// это плановое завершение контракта (его длина), а не быстрота поставки; иначе фильтр
// «до N дней» врал бы (у большинства ЕИС там дата на месяцы вперёд). Настоящий срок
// поставки у ЕИС — во вложенном проекте контракта, на common-info в днях его нет.
// Иначе — null («срок неизвестен»). Заполняется лишь для закупок, в карточку которых
// и так заходим (ближайшие docsLimit).
function extractDeliveryDays(html) {
  const val = cardField(html, /^Срок исполнения контракта$/i);
  if (!val) return null;
  // ВНИМАНИЕ: класс кириллицы задаём явно [а-яё], а НЕ \w — в JS \w это только
  // [A-Za-z0-9_] и «рабочих»/«календарных» им не матчатся (в Python \w — Unicode,
  // из-за чего баг сначала не выявился: тест зелёный, а в проде срок выходил null).
  const d = /(\d+)\s*(?:рабоч|календарн)[а-яё]*\s*дн/i.exec(val);
  return d ? Number(d[1]) : null;
}

// Вкладка документов 44-ФЗ открывается ПРЯМО, без захода в карточку: у неё тот
// же адрес, только common-info.html → documents.html. Проверено на 15 закупках —
// 14 отдали документ-в-документ то же, что двухшаговый путь, одна отдала больше
// (в кэше лежало 20 из-за потолка в fetchDocs). Это половина стоимости: один
// запрос вместо двух, а 44-ФЗ — 68% закупок ЕИС.
//
// У 223-ФЗ так нельзя: их documents.html требует noticeGuid, который есть только
// в карточке (без него — 301 на страницу, отдающую 404). Возвращаем null, и
// вызывающий идёт длинным путём.
function docsUrlDirect(link) {
  return /\/epz\/order\/notice\/[a-z0-9]+\/view\/common-info\.html\?regNumber=\d+/i.test(link || "")
    ? link.replace("common-info.html", "documents.html")
    : null;
}

// со страницы карточки: ссылка на вкладку документов + регион + срок (один заход)
async function fetchCardMeta(link) {
  try {
    const html = await curlText(link);
    let u = (/href="([^"]*documents\.html[^"]*)"/i.exec(html) || [])[1];
    if (u && u.startsWith("/")) u = "https://zakupki.gov.ru" + u;
    return {
      docsUrl: u || null, region: extractRegion(html), deliveryDays: extractDeliveryDays(html),
      procStage: extractProcStage(html),
    };
  } catch (e) { return { docsUrl: null, region: "", deliveryDays: null, procStage: "" }; }
}
function extractProcStage(html) {
  return stripTags((/header-mid__title[^>]*>([\s\S]*?)<\/div>/i.exec(html) || [])[1]) || "";
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

function toPurchase(it, documents, region, deliveryDays, regionGuessed) {
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
    // регион не из карточки, а угадан по номеру (см. eisregion.js) — точность
    // 98.4% на отложенной выборке, но это всё же догадка, и она помечена
    ...(regionGuessed ? { regionGuessed: true } : {}),
    endDate: it.endIso,
    beginDate: it.pubIso,
    deadlineDays: end ? Math.max(0, Math.ceil((end - now) / DAY)) : 0,
    publishedDaysAgo: it.pubIso ? Math.max(0, Math.round((now - new Date(it.pubIso).getTime()) / DAY)) : 0,
    guaranteeApp: 0, guaranteeContract: 0, prepayment: 0,
    href: it.link,
    procedureType: it.procedureType || "", competitive: it.competitive !== false,
    procStage: it.procStage || "",
    deliveryDays: deliveryDays ?? null, deliveryPlace: "",
    lots: [{ name: it.title, qty: "—", unit: "", price: it.price }],
    documents: documents || [],
    matches: {},
  };
}

// Прогоняет постраничный сбор results.html (опционально с ключевым словом) в
// общий Set/массив items, пока не наберёт take штук или страницы не кончатся.
// browserPage передан → тянем через Chromium (для поиска), иначе — curl.
async function fetchPool(seen, items, take, searchString, label, browserPage) {
  const maxPages = Math.ceil(take / 50) + 1;
  let got = 0;
  for (let page = 1; page <= maxPages && got < take; page++) {
    let parsed;
    try {
      const html = browserPage
        ? await fetchResultsPageViaBrowser(browserPage, page, searchString)
        : await fetchResultsPage(page, searchString);
      parsed = parseHtmlItems(html);
    } catch (e) { break; }
    if (!parsed.length) break;
    for (const it of parsed) {
      if (seen.has(it.number)) continue;
      // откуда закупка пришла: прицельный поиск по теме или общий пул. Флаг нужен
      // на шаге 3 — бюджет на документы тратим сначала на тематические (ради них
      // поиск и делался), иначе они тонут в общем пуле и остаются без ТЗ.
      // `seen` не даст общему проходу перетереть флаг: закупка добавляется один раз.
      it.viaKeyword = Boolean(searchString);
      seen.add(it.number); items.push(it); got++;
    }
    process.stdout.write(`\r  ЕИС ${label}: ${got}/${take}`);
  }
  process.stdout.write("\n");
}

// Один дата-срез: страницы подряд от того места, где остановился прошлый прогон.
// Останавливаемся по любому из четырёх поводов, и они РАЗНЫЕ по смыслу:
//   • budget/maxPages — на сегодня хватит, срез не дочитан, вернёмся;
//   • пустая страница — срез вычерпан, больше сюда не ходим;
//   • CLAMP_PAGE — дальше выдача врёт (клон последней настоящей страницы), и
//     отличить это от честных данных по содержимому нельзя, только по номеру.
// Сетевую ошибку НЕ считаем концом среза: курсор остаётся на несработавшей
// странице, иначе одно моргание канала навсегда выкинуло бы кусок корпуса.
const CLAMP_PAGE = 100;

// fetchPage — шов для тестов: логику остановки надо проверять без сети, иначе
// единственный способ узнать, что курсор поехал, — авария в проде через сутки.
async function sweepSlice(seen, items, slice, budget, maxPages = 12, fetchPage = fetchResultsPage) {
  let page = slice.page || 1;
  let got = 0, pages = 0, done = false;
  while (pages < maxPages && got < budget && page < CLAMP_PAGE) {
    let parsed;
    try {
      parsed = parseHtmlItems(await fetchPage(page, undefined, slice));
    } catch (e) { break; }
    pages++;
    if (!parsed.length) { done = true; break; }
    for (const it of parsed) {
      if (seen.has(it.number)) continue;
      it.viaKeyword = false;
      seen.add(it.number); items.push(it); got++;
    }
    page++;
  }
  if (page >= CLAMP_PAGE) done = true;
  return { nextPage: page, got, pages, done };
}

// docsCache — накопительный кэш «карточка ЕИС → документы/регион/срок поставки»,
// переживающий пересборку снапшота (см. шаг 3). Владелец файла — build_snapshot.js,
// сюда приходит уже загруженным и мутируется на месте.
async function collectEis(listLimit = 600, docsLimit = 150, keywords = DEFAULT_KEYWORDS,
                          docsCache = {}, regionIndex = new Map(), sweep = null) {
  const seen = new Set();
  let items = [];

  // 1) прицельные проходы по ключевым словам — иначе узкие темы (медицина и т.п.)
  // почти не попадают в общий пул «последние обновлённые по всем категориям».
  // Через настоящий Chromium (Playwright) — источник глушит поиск для голых HTTP-
  // клиентов вроде curl (см. комментарий у fetchResultsPageViaBrowser); если
  // Playwright не поднялся (не установлен на этой машине и т.п.) — тихо откатываемся
  // на curl, чтобы сборка не падала целиком, просто без гарантии по этим темам.
  // Пауза между проходами — 30 разных поисковых запросов подряд без пауз выглядит
  // как обстрел, а не как обычный просмотр; так честнее к источнику.
  const perKeyword = Number(process.env.LK_EIS_KEYWORD_TAKE || 60);
  const KEYWORD_PAUSE_MS = Number(process.env.LK_EIS_KEYWORD_PAUSE_MS || 400);
  if (keywords.length) {
    let browserPage = null;
    try {
      const browser = await getBrowser();
      const ctx = await browser.newContext({ locale: "ru-RU" });
      browserPage = await ctx.newPage();
    } catch (e) {
      console.error("ЕИС: Chromium не поднялся, поиск по темам — через curl —", e.message);
    }
    for (const kw of keywords) {
      await fetchPool(seen, items, perKeyword, kw, `«${kw}»`, browserPage);
      await sleep(KEYWORD_PAUSE_MS + Math.floor(Math.random() * KEYWORD_PAUSE_MS));
    }
  }

  // 2) общий список (без поиска) — источник не глушит, оставляем на curl (дешевле)
  await fetchPool(seen, items, listLimit, undefined, "список");
  await closeBrowser();

  // 2б) дата-срезы — единственный способ уйти глубже 5000 «последних обновлённых».
  // Каждый прогон дочитывает несколько дней публикации с того места, где встал
  // прошлый; состояние живёт в файле (см. eissweep.js). Идём последовательно и с
  // общим бюджетом записей: приток, обгоняющий бюджет документов, только копит
  // закупки со статусом «ждёт очереди».
  if (sweep) {
    const slices = enumerateSlices(Date.now());
    pruneSweep(sweep, slices);
    const picked = pickSlices(sweep, slices);
    let left = SWEEP_TAKE, swept = 0;
    for (const slice of picked) {
      if (left <= 0) break;
      const res = await sweepSlice(seen, items, slice, left, SLICE_PAGES);
      advance(sweep, slice, res);
      left -= res.got; swept += res.got;
      process.stdout.write(`\r  ЕИС срез ${slice.law}-ФЗ ${slice.day}: +${res.got} `
        + `(стр. ${slice.page}–${res.nextPage - 1}${res.done ? ", вычерпан" : ""})\n`);
    }
    const st = sweepStats(sweep, slices);
    console.log(`  ЕИС срезы: +${swept} записей за ${picked.length} срезов | `
      + `вычерпано ${st.done} из ${st.total} (тронуто ${st.touched})`);
  }

  // только с будущим сроком, ближайшие сверху
  const now = Date.now();
  items = items
    .filter(it => it.endIso && new Date(it.endIso).getTime() > now)
    .sort((a, b) => new Date(a.endIso) - new Date(b.endIso));

  // 3) документы + регион — один заход в карточку на закупку, но их тысячи, а
  // каждая стоит два запроса (карточка → documents.html). Поэтому бюджет docsLimit,
  // и тратится он с учётом того, что уже добыто в прошлые прогоны: `docsCache`
  // переживает пересборку, так что за сутки ежечасных прогонов покрытие
  // накапливается до почти полного, а длительность одного прогона не растёт.
  //
  // Очередь по убыванию пользы:
  //   1. тематические, которых ещё нет в кэше — ради них и делался поиск по словам;
  //   2. остальные новые — по близости дедлайна (items уже так отсортированы);
  //   3. остатком бюджета — обновление протухших: ТЗ закупок правят, и кэш
  //      возрастом больше TTL перепроверяем (см. matcher/src/versioning.py).
  const fresh = [], stale = [];
  for (const it of items) {
    const c = docsCache[it.number];
    if (!c) fresh.push(it);
    else if (now - (c.fetchedAt || 0) > DOCS_TTL_MS) stale.push(it);
  }
  // сортировка стабильна (Node 11+), поэтому внутри каждой группы порядок по
  // дедлайну, заданный выше, сохраняется — тематические просто поднимаются наверх
  fresh.sort((a, b) => Number(Boolean(b.viaKeyword)) - Number(Boolean(a.viaKeyword)));
  const queue = fresh.concat(stale).slice(0, docsLimit);

  // Коротким путём (documents.html напрямую, один запрос) идём только там, где
  // карточка больше ни за чем не нужна — то есть регион уже выводится из номера.
  // Незнакомый код региона отправляет закупку длинным путём, и её карточка
  // пополняет справочник: так он дообучается сам, вместо зашитой таблицы,
  // которая устарела бы молча. Срок поставки при коротком пути теряем осознанно —
  // он был заполнен лишь у 8% посещённых карточек, а стоил целого запроса.
  for (const it of queue) {
    it.directDocsUrl = regionFromNumber(it.number, regionIndex) ? docsUrlDirect(it.link) : null;
  }
  const direct = queue.filter(it => it.directDocsUrl).length;

  await mapLimit(queue, CONC, async (it) => {
    if (it.directDocsUrl) {
      docsCache[it.number] = {
        docs: await fetchDocs(it.directDocsUrl),
        region: "", deliveryDays: null, direct: true, fetchedAt: Date.now(),
      };
      return;
    }
    const meta = await fetchCardMeta(it.link);
    docsCache[it.number] = {
      docs: meta.docsUrl ? await fetchDocs(meta.docsUrl) : [],
      region: meta.region || "",
      deliveryDays: meta.deliveryDays ?? null,
      procStage: meta.procStage || "",
      fetchedAt: Date.now(),
    };
  }, (k, t) => process.stdout.write(`\r  ЕИС документы: ${k}/${t}`));
  if (queue.length) process.stdout.write("\n");

  const queued = new Set(queue.map(it => it.number));
  const reused = items.filter(it => docsCache[it.number] && !queued.has(it.number)).length;
  console.log(`  ЕИС карточки: ${queue.length} запрошено (новых ${fresh.length}, `
    + `тематических ${fresh.filter(it => it.viaKeyword).length}, протухших ${stale.length}), `
    + `${reused} из кэша | коротким путём ${direct} из ${queue.length}, `
    + `сэкономлено запросов ${direct}`);

  let guessed = 0;
  const out = items.map(it => {
    const m = docsCache[it.number];
    let region = (m && m.region) || "";
    let regionGuessed = false;
    // Регион из карточки точнее — догадку применяем только когда его нет.
    if (!region) {
      region = regionFromNumber(it.number, regionIndex);
      if (region) { regionGuessed = true; guessed++; }
    }
    // Список свежее карточки (её могли не заходить неделю — см. DOCS_TTL_MS),
    // поэтому этап из СПИСКА в приоритете, карточка — только фолбэк.
    it.procStage = it.procStage || (m && m.procStage) || "";
    const p = toPurchase(it, (m && m.docs) || [], region, (m && m.deliveryDays) ?? null, regionGuessed);
    // Заходили ли мы вообще в карточку. Без этого пустой documents[] неотличим от
    // «приложений нет», и «Умная сверка» говорила бы «у закупки нет документов» тем
    // тысячам закупок, до которых просто не дошёл бюджет docsLimit. Это разные вещи:
    // первое — свойство закупки, второе — наша недоработка, и врать тут нельзя.
    p.docsFetched = Boolean(m);
    return p;
  });
  console.log(`  ЕИС регионы: ${out.filter(p => p.region && !p.regionGuessed).length} из карточек, `
    + `${guessed} угадано по номеру (справочник — ${regionIndex.size} кодов), `
    + `${out.filter(p => !p.region).length} без региона`);
  return out;
}

// Точечная перепроверка этапа закупки — независимо от бюджета документов и его
// TTL. Список даёт этап только тем ~2-3 тыс. закупкам, что попали в свежее окно
// ИЛИ карточка даёт его тем, что как раз сейчас идут за документами — а
// накопитель держит до 40 тыс., и большинство туда не попадает неделями (весь
// смысл накопителя — не выселять их, пока жив срок). Комиссия начинает работу
// РАНЬШЕ формальной даты (живой пример — 32616298437 «Приобретение GPS
// оборудования»: в снапшоте «9 часов до конца», на ЕИС уже «Работа комиссии»),
// и без отдельного захода узнать об этом неоткуда, пока запись не истечёт сама.
// Проверяем только тех, кому это вообще нужно и у кого срок близко — там риск
// «просрочили молча» выше всего, а не размазываем бюджет по всему накопителю.
const STAGE_WINDOW_DAYS = Number(process.env.LK_EIS_STAGE_WINDOW_DAYS || 3);
async function refreshStages(purchases, budget = 300) {
  const now = Date.now();
  const windowMs = STAGE_WINDOW_DAYS * DAY;
  const candidates = purchases.filter(p => (
    p.id && String(p.id).startsWith("eis_") && p.href && p.competitive !== false
    && p.endDate && (() => {
      const end = new Date(p.endDate).getTime();
      return end > now && end - now <= windowMs;
    })()
  )).sort((a, b) => new Date(a.endDate) - new Date(b.endDate)).slice(0, budget);

  let checked = 0, updated = 0;
  await mapLimit(candidates, CONC, async (p) => {
    try {
      const html = await curlText(p.href);
      const stage = extractProcStage(html);
      checked++;
      if (stage && stage !== p.procStage) { p.procStage = stage; updated++; }
    } catch (e) { /* сеть подвела — оставляем прежнее значение, не молчание */ }
  }, candidates.length ? (k, t) => process.stdout.write(`\r  этап закупки: ${k}/${t}`) : undefined);
  if (candidates.length) process.stdout.write("\n");
  return { checked, updated, candidates: candidates.length };
}

module.exports = { collectEis, refreshStages, DEFAULT_KEYWORDS, docsUrlDirect, sweepSlice, CLAMP_PAGE };
