// Официальный интеграционный API ЕИС (сервис getDocsIP) через ГОСТ-TLS туннель.
//
// Зачем: list-скрейпинг ЕИС упирается в потолок 5000 и вращающееся окно (грабли
// §10/§12), а добыча документов идёт дорогим заходом в HTML-карточку. Официальный
// API отдаёт по реестровому номеру ИЛИ по региону+типу+дню ZIP с XML всего
// жизненного цикла закупки: метаданные, контакты, и прямые ссылки на документы —
// одним вызовом, без антибота и без потолка.
//
// Транспорт: браузер/curl не открывают int.zakupki.gov.ru напрямую — там только
// ГОСТ-TLS (ERR_CONNECTION_CLOSED — это норма, не сбой сети). Локальный
// stunnel-msspi принимает обычный HTTP на 127.0.0.1:1443, заворачивает в ГОСТ-TLS
// к int.zakupki.gov.ru:443 и подставляет КЭП как клиентский сертификат. Поэтому
// ходим на туннель с заголовком Host: int.zakupki.gov.ru.
//
// Модуль НЕ подключён к build_snapshot.js — это отдельный слой, включается по
// решению (см. memory scale-to-50k-and-eis-api: scope A обогащение / B замена ленты).

const { execFile } = require("child_process");
const zlib = require("zlib");
const crypto = require("crypto");

// --- конфиг (секреты только из env, в git не коммитим) ---
const TOKEN = process.env.LK_EIS_TOKEN || "";
const TUNNEL = process.env.LK_EIS_TUNNEL || "127.0.0.1:1443";
const HOST = process.env.LK_EIS_HOST || "int.zakupki.gov.ru";
const SERVICE_PATH = process.env.LK_EIS_SERVICE_PATH || "/eis-integration/services/getDocsIP";
const SOAP_ACTION = "http://zakupki.gov.ru/fz44/queue/ws/get-docs-ip";
const WS_NS = "http://zakupki.gov.ru/fz44/get-docs-ip/ws";
const POST_TIMEOUT = Number(process.env.LK_EIS_API_TIMEOUT || 40);
const DL_TIMEOUT = Number(process.env.LK_EIS_DL_TIMEOUT || 120);
const RETRIES = Number(process.env.LK_CURL_RETRIES ?? 2);
const RETRY_DELAY_MS = Number(process.env.LK_CURL_RETRY_DELAY_MS || 800);

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function xmlEscape(s) {
  return String(s).replace(/[<>&]/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]));
}
// createDateTime в схеме — xs:dateTime; ЕИС не любит миллисекунды в этом поле.
function nowIso() { return new Date().toISOString().replace(/\.\d+Z$/, "Z"); }

// --- тонкий curl-раннер через туннель (свой, а не util.curlAsync: там жёстко
// зашиты --max-time 30 и браузерные заголовки, а тут нужен Host, бинарь, свой
// таймаут и никакой мимикрии — это наш собственный аутентифицированный канал) ---
function curlOnce(args, { binary = false, timeout = POST_TIMEOUT } = {}) {
  return new Promise((resolve, reject) => {
    execFile("curl",
      ["-s", "-S", "--max-time", String(timeout), ...args],
      { maxBuffer: 128 * 1024 * 1024, encoding: binary ? "buffer" : "utf8" },
      (err, stdout) => (err ? reject(err) : resolve(stdout)));
  });
}
async function curlTunnel(args, opts) {
  let lastErr;
  for (let attempt = 0; attempt <= RETRIES; attempt++) {
    try { return await curlOnce(args, opts); }
    catch (e) { lastErr = e; if (attempt < RETRIES) await sleep(RETRY_DELAY_MS * (attempt + 1)); }
  }
  throw lastErr;
}

// --- построители SOAP-конверта ---
// ⚠️ subsystemType живёт в <selectionParams>, а НЕ в <index>; <index> требует
// id/createDateTime/mode (см. XSD getDocsIP-ws-api). Ошибка стоила пустого ответа.
function indexBlock() {
  return `<index><id>${crypto.randomUUID()}</id>` +
    `<createDateTime>${nowIso()}</createDateTime><mode>PROD</mode></index>`;
}
function envelope(bodyInner) {
  return `<?xml version="1.0" encoding="UTF-8"?>` +
    `<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ws="${WS_NS}">` +
    `<soapenv:Header><individualPerson_token>${xmlEscape(TOKEN)}</individualPerson_token></soapenv:Header>` +
    `<soapenv:Body>${bodyInner}</soapenv:Body></soapenv:Envelope>`;
}

function buildReestrRequest(reestrNumber, subsystemType = "PRIZ") {
  return envelope(
    `<ws:getDocsByReestrNumberRequest>${indexBlock()}` +
    `<selectionParams>` +
    `<subsystemType>${xmlEscape(subsystemType)}</subsystemType>` +
    `<reestrNumber>${xmlEscape(reestrNumber)}</reestrNumber>` +
    `</selectionParams></ws:getDocsByReestrNumberRequest>`);
}

// law: "44" → documentType44, "223" → documentType223. period: {exactDate:"YYYY-MM-DD"}
// или {fromHour:"YYYY-MM-DDTHH", ...} — пока поддержан exactDate (день).
function buildOrgRegionRequest(orgRegion, documentType, exactDate, { subsystemType = "PRIZ", law = "44" } = {}) {
  const dtTag = law === "223" ? "documentType223" : "documentType44";
  return envelope(
    `<ws:getDocsByOrgRegionRequest>${indexBlock()}` +
    `<selectionParams>` +
    `<orgRegion>${xmlEscape(orgRegion)}</orgRegion>` +
    `<subsystemType>${xmlEscape(subsystemType)}</subsystemType>` +
    `<${dtTag}>${xmlEscape(documentType)}</${dtTag}>` +
    `<periodInfo><exactDate>${xmlEscape(exactDate)}</exactDate></periodInfo>` +
    `</selectionParams></ws:getDocsByOrgRegionRequest>`);
}

// --- разбор SOAP-ответа: archiveUrl(ы) | noData | errorInfo ---
function parseResponse(xml) {
  const s = String(xml);
  const err = s.match(/<(?:\w+:)?errorInfo[ >]([\s\S]*?)<\/(?:\w+:)?errorInfo>/);
  if (err) {
    const code = (err[1].match(/<(?:\w+:)?code>([\s\S]*?)<\/(?:\w+:)?code>/) || [])[1];
    const msg = (err[1].match(/<(?:\w+:)?(?:message|errorMessage)>([\s\S]*?)<\/(?:\w+:)?(?:message|errorMessage)>/) || [])[1];
    return { archiveUrls: [], error: { code: code && code.trim(), message: msg && msg.trim() } };
  }
  if (/<(?:\w+:)?noData[ >/]/.test(s)) return { archiveUrls: [], noData: true };
  const urls = [];
  const re = /<(?:\w+:)?archiveUrl[^>]*>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/(?:\w+:)?archiveUrl>/g;
  let m;
  while ((m = re.exec(s))) urls.push(m[1].trim());
  return { archiveUrls: urls };
}

// --- HTTP-вызовы ---
async function postSoap(xml) {
  if (!TOKEN) throw new Error("LK_EIS_TOKEN не задан (положите токен сервисов отдачи в .env)");
  const url = `http://${TUNNEL}${SERVICE_PATH}`;
  return curlTunnel([
    "-X", "POST", url,
    "-H", `Host: ${HOST}`,
    "-H", "Content-Type: text/xml; charset=utf-8",
    "-H", `SOAPAction: ${SOAP_ACTION}`,
    "--data-binary", xml,
  ], { binary: false, timeout: POST_TIMEOUT });
}

// archiveUrl приходит абсолютным (https://int.zakupki.gov.ru/dstore/...). Заворачиваем
// через туннель: меняем схему+хост на локальный порт, реальный хост уносим в Host.
function tunnelize(absUrl) {
  const u = new URL(absUrl);
  return { path: u.pathname + u.search, host: u.host };
}
async function downloadArchive(absUrl) {
  const { path, host } = tunnelize(absUrl);
  return curlTunnel([
    `http://${TUNNEL}${path}`,
    "-H", `Host: ${host}`,
    "-H", `individualPerson_token: ${TOKEN}`,
  ], { binary: true, timeout: DL_TIMEOUT });
}

// --- мини-распаковщик ZIP (без внешних зависимостей) ---
// Читает central directory → local headers → inflateRaw (метод 8) / копия (метод 0).
// Хватает для архивов ЕИС (deflate). Не поддерживает ZIP64/шифрование — их тут нет.
function unzip(buf) {
  if (!Buffer.isBuffer(buf)) buf = Buffer.from(buf);
  // End Of Central Directory: сигнатура 0x06054b50, ищем с конца (комментарий редок).
  let eocd = -1;
  for (let i = buf.length - 22; i >= 0 && i >= buf.length - 22 - 0xffff; i--) {
    if (buf.readUInt32LE(i) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error("ZIP: не найден EOCD (файл не ZIP или обрезан)");
  const count = buf.readUInt16LE(eocd + 10);
  let off = buf.readUInt32LE(eocd + 16);
  const out = [];
  for (let e = 0; e < count; e++) {
    if (buf.readUInt32LE(off) !== 0x02014b50) throw new Error("ZIP: битая central directory");
    const method = buf.readUInt16LE(off + 10);
    const compSize = buf.readUInt32LE(off + 20);
    const nameLen = buf.readUInt16LE(off + 28);
    const extraLen = buf.readUInt16LE(off + 30);
    const commentLen = buf.readUInt16LE(off + 32);
    const localOff = buf.readUInt32LE(off + 42);
    const name = buf.toString("utf8", off + 46, off + 46 + nameLen);
    // Локальный заголовок: свои длины name/extra (могут отличаться от central).
    if (buf.readUInt32LE(localOff) !== 0x04034b50) throw new Error("ZIP: битый local header");
    const lNameLen = buf.readUInt16LE(localOff + 26);
    const lExtraLen = buf.readUInt16LE(localOff + 28);
    const dataStart = localOff + 30 + lNameLen + lExtraLen;
    const comp = buf.subarray(dataStart, dataStart + compSize);
    let data;
    if (method === 0) data = Buffer.from(comp);
    else if (method === 8) data = zlib.inflateRawSync(comp);
    else throw new Error("ZIP: неподдерживаемый метод сжатия " + method);
    if (!name.endsWith("/")) out.push({ name, data });
    off += 46 + nameLen + extraLen + commentLen;
  }
  return out;
}

// --- разбор XML извещения 44-ФЗ (epNotification*) в нашу схему ---
// ns-агностик: теги идут с префиксами ns2:/ns4:/ns5:, берём по локальному имени.
function grab(xml, tag) {
  const m = xml.match(new RegExp("<(?:\\w+:)?" + tag + "[ >]([\\s\\S]*?)</(?:\\w+:)?" + tag + ">"));
  return m ? m[1] : null;
}
function grabText(xml, tag) {
  const raw = grab(xml, tag);
  if (raw == null) return null;
  const t = raw.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  return t || null;
}

function parseNotification(xml) {
  const s = String(xml).replace(/\r?\n/g, "\n");
  const number = grabText(s, "purchaseNumber");
  const title = grabText(s, "purchaseObjectInfo");

  // Заказчик: ответственная организация (purchaseResponsibleInfo/responsibleOrgInfo).
  const respBlock = grab(s, "responsibleOrgInfo") || s;
  const customer = grabText(respBlock, "fullName");
  const customerInn = grabText(respBlock, "INN");

  // Контакты — раньше для 44-ФЗ добывались только заходом в карточку.
  const cp = grab(s, "contactPersonInfo") || "";
  const person = [grabText(cp, "lastName"), grabText(cp, "firstName"), grabText(cp, "middleName")]
    .filter(Boolean).join(" ") || null;
  const contacts = {
    person,
    email: grabText(s, "contactEMail"),
    phone: grabText(s, "contactPhone"),
  };
  const hasContacts = contacts.person || contacts.email || contacts.phone;

  // Цена — внутри maxPriceInfo.
  const priceBlock = grab(s, "maxPriceInfo") || s;
  const priceRaw = grabText(priceBlock, "maxPrice");
  const price = priceRaw != null ? Number(priceRaw) : null;

  // Документы: attachmentInfo с fileName + прямым публичным url (качается как сейчас).
  const documents = [];
  const attRe = /<(?:\w+:)?attachmentInfo[ >]([\s\S]*?)<\/(?:\w+:)?attachmentInfo>/g;
  let a;
  while ((a = attRe.exec(s))) {
    const blk = a[1];
    const name = grabText(blk, "fileName");
    const url = grabText(blk, "url");
    const id = grabText(blk, "publishedContentId");
    if (url) documents.push({ id: id || url, name: name || "", url });
  }

  return {
    number,
    title,
    law: "44",
    source: "eis-api",
    customer,
    customerInn,
    price: Number.isFinite(price) ? price : null,
    href: grabText(s, "href"),
    beginDate: grabText(s, "startDT"),
    endDate: grabText(s, "endDT"),
    publishDate: grabText(s, "plannedPublishDate"),
    summarizingDate: grabText(s, "summarizingDate"),
    contacts: hasContacts ? contacts : null,
    documents,
  };
}

// Проба туннеля: доступен ли ГОСТ-TLS шлюз прямо сейчас (stunnel может быть не
// запущен — тогда обогащение откатывается на скрейпинг, а не падает). Дёшево:
// GET WSDL сервиса. Возвращает true только если ответ похож на WSDL.
async function ping() {
  if (!TOKEN) return false;
  try {
    const wsdl = await curlTunnel([
      `http://${TUNNEL}${SERVICE_PATH}?wsdl`, "-H", `Host: ${HOST}`,
    ], { binary: false, timeout: 15 });
    return /wsdl:definitions|<definitions/i.test(String(wsdl));
  } catch (e) { return false; }
}

// В архиве лежат несколько XML жизненного цикла — извещение это epNotification*,
// и версий может быть несколько: берём с наибольшим _N_ в имени (последнюю ревизию).
function pickNotification(entries) {
  const notes = entries.filter(e => /epNotification/i.test(e.name));
  if (!notes.length) return null;
  notes.sort((x, y) => (verOf(y.name) - verOf(x.name)));
  return notes[0];
}
function verOf(name) {
  const m = name.match(/_(\d+)_[0-9A-F]+\.xml$/i);
  return m ? Number(m[1]) : 0;
}

// --- высокоуровневые операции ---
// Обогащение одной закупки: reestrNumber → распакованное извещение в нашей схеме.
async function fetchByReestr(reestrNumber, opts = {}) {
  const resp = parseResponse(await postSoap(buildReestrRequest(reestrNumber, opts.subsystemType)));
  if (resp.error) return { error: resp.error };
  if (resp.noData || !resp.archiveUrls.length) return { noData: true };
  const entries = [];
  for (const url of resp.archiveUrls) {
    try { entries.push(...unzip(await downloadArchive(url))); }
    catch (e) { /* один битый архив не должен рушить остальные */ }
  }
  const note = pickNotification(entries);
  return {
    purchase: note ? parseNotification(note.data.toString("utf8")) : null,
    entries: entries.map(e => e.name),
    archiveUrls: resp.archiveUrls,
  };
}

// Массовая выдача: регион+тип+день → список распакованных извещений.
async function fetchByOrgRegion(orgRegion, documentType, exactDate, opts = {}) {
  const resp = parseResponse(await postSoap(
    buildOrgRegionRequest(orgRegion, documentType, exactDate, opts)));
  if (resp.error) return { error: resp.error };
  if (resp.noData || !resp.archiveUrls.length) return { noData: true, purchases: [] };
  const entries = [];
  for (const url of resp.archiveUrls) {
    try { entries.push(...unzip(await downloadArchive(url))); }
    catch (e) { /* пропускаем битый архив */ }
  }
  const purchases = entries
    .filter(e => /epNotification/i.test(e.name))
    .map(e => { try { return parseNotification(e.data.toString("utf8")); } catch { return null; } })
    .filter(Boolean);
  return { purchases, archiveUrls: resp.archiveUrls };
}

module.exports = {
  // низкоуровневое — для тестов и переиспользования
  buildReestrRequest, buildOrgRegionRequest, envelope, indexBlock,
  parseResponse, unzip, parseNotification, pickNotification, tunnelize,
  // высокоуровневое
  ping, postSoap, downloadArchive, fetchByReestr, fetchByOrgRegion,
  config: { TUNNEL, HOST, SERVICE_PATH, hasToken: !!TOKEN },
};
