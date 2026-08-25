// Тесты адаптера официального API ЕИС (eisapi.js). Сеть не трогают: проверяют
// построители SOAP, разбор ответа, мини-распаковщик ZIP и разбор XML извещения.
// Живой прогон через туннель — отдельно (scratchpad), не здесь.

const test = require("node:test");
const assert = require("node:assert");
const zlib = require("zlib");
const api = require("./sources/eisapi");

// --- построители конверта ---

test("buildReestrRequest: subsystemType в selectionParams, index с id/mode", () => {
  const xml = api.buildReestrRequest("0145300010026000234", "PRIZ");
  // index обязан нести id, createDateTime, mode
  assert.match(xml, /<index><id>[0-9a-f-]{36}<\/id><createDateTime>[^<]+<\/createDateTime><mode>PROD<\/mode><\/index>/);
  // subsystemType — внутри selectionParams, НЕ в index (грабля: пустой ответ)
  const sel = xml.match(/<selectionParams>([\s\S]*?)<\/selectionParams>/)[1];
  assert.match(sel, /<subsystemType>PRIZ<\/subsystemType>/);
  assert.match(sel, /<reestrNumber>0145300010026000234<\/reestrNumber>/);
  assert.doesNotMatch(xml.match(/<index>[\s\S]*?<\/index>/)[0], /subsystemType/);
  // токен в заголовке
  assert.match(xml, /<individualPerson_token>/);
});

test("buildOrgRegionRequest: documentType44 vs 223 по закону", () => {
  const a44 = api.buildOrgRegionRequest("47", "epNotificationEOK2020", "2026-08-11", { law: "44" });
  assert.match(a44, /<documentType44>epNotificationEOK2020<\/documentType44>/);
  assert.match(a44, /<orgRegion>47<\/orgRegion>/);
  assert.match(a44, /<periodInfo><exactDate>2026-08-11<\/exactDate><\/periodInfo>/);
  const a223 = api.buildOrgRegionRequest("77", "epNotificationEZ44", "2026-08-11", { law: "223" });
  assert.match(a223, /<documentType223>/);
});

test("envelope: экранирует спецсимволы в токене", () => {
  // косвенно: envelope не ломается на & в значениях
  const xml = api.envelope("<x>a &amp; b</x>");
  assert.match(xml, /<soapenv:Body><x>a &amp; b<\/x><\/soapenv:Body>/);
});

// --- разбор ответа ---

test("parseResponse: извлекает archiveUrl из CDATA", () => {
  const resp = `<soap:Envelope><soap:Body><ns2:getDocsByReestrNumberResponse>
    <dataInfo><archiveUrl><![CDATA[https://int.zakupki.gov.ru/dstore/common/download/compound?docRequestUid=X&compoundUid=Y]]></archiveUrl></dataInfo>
    </ns2:getDocsByReestrNumberResponse></soap:Body></soap:Envelope>`;
  const r = api.parseResponse(resp);
  assert.equal(r.archiveUrls.length, 1);
  assert.equal(r.archiveUrls[0], "https://int.zakupki.gov.ru/dstore/common/download/compound?docRequestUid=X&compoundUid=Y");
});

test("parseResponse: несколько archiveUrl", () => {
  const resp = `<dataInfo><archiveUrl>u1</archiveUrl><archiveUrl>u2</archiveUrl></dataInfo>`;
  assert.deepEqual(api.parseResponse(resp).archiveUrls, ["u1", "u2"]);
});

test("parseResponse: noData", () => {
  assert.equal(api.parseResponse(`<dataInfo><noData>true</noData></dataInfo>`).noData, true);
});

test("parseResponse: errorInfo с кодом", () => {
  const r = api.parseResponse(`<dataInfo><errorInfo><code>5</code><message>Токены отсутствуют</message></errorInfo></dataInfo>`);
  assert.equal(r.error.code, "5");
  assert.match(r.error.message, /Токены/);
  assert.equal(r.archiveUrls.length, 0);
});

// --- мини-распаковщик ZIP ---
// Собираем валидный ZIP руками (STORED + DEFLATE) и распаковываем обратно.

function makeZip(files) {
  // files: [{name, data:Buffer, deflate:bool}]
  const locals = [];
  const centrals = [];
  let offset = 0;
  for (const f of files) {
    const nameBuf = Buffer.from(f.name, "utf8");
    const method = f.deflate ? 8 : 0;
    const stored = f.deflate ? zlib.deflateRawSync(f.data) : f.data;
    const lh = Buffer.alloc(30);
    lh.writeUInt32LE(0x04034b50, 0);
    lh.writeUInt16LE(20, 4);
    lh.writeUInt16LE(method, 8);
    lh.writeUInt32LE(0, 14);              // crc (ридер не проверяет)
    lh.writeUInt32LE(stored.length, 18);  // comp size
    lh.writeUInt32LE(f.data.length, 22);  // uncomp size
    lh.writeUInt16LE(nameBuf.length, 26);
    const localOff = offset;
    locals.push(lh, nameBuf, stored);
    offset += 30 + nameBuf.length + stored.length;

    const ch = Buffer.alloc(46);
    ch.writeUInt32LE(0x02014b50, 0);
    ch.writeUInt16LE(20, 4);
    ch.writeUInt16LE(20, 6);
    ch.writeUInt16LE(method, 10);
    ch.writeUInt32LE(0, 16);
    ch.writeUInt32LE(stored.length, 20);
    ch.writeUInt32LE(f.data.length, 24);
    ch.writeUInt16LE(nameBuf.length, 28);
    ch.writeUInt32LE(localOff, 42);
    centrals.push(ch, nameBuf);
  }
  const cd = Buffer.concat(centrals);
  const cdOffset = offset;
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(files.length, 8);
  eocd.writeUInt16LE(files.length, 10);
  eocd.writeUInt32LE(cd.length, 12);
  eocd.writeUInt32LE(cdOffset, 16);
  return Buffer.concat([...locals, cd, eocd]);
}

test("unzip: STORED + DEFLATE round-trip", () => {
  const big = Buffer.from("<xml>" + "повтор ".repeat(500) + "</xml>", "utf8");
  const zip = makeZip([
    { name: "a.xml", data: Buffer.from("привет", "utf8"), deflate: false },
    { name: "b.xml", data: big, deflate: true },
  ]);
  const out = api.unzip(zip);
  assert.equal(out.length, 2);
  assert.equal(out.find(e => e.name === "a.xml").data.toString("utf8"), "привет");
  assert.equal(out.find(e => e.name === "b.xml").data.toString("utf8"), big.toString("utf8"));
});

test("unzip: пропускает каталоги (имена на /)", () => {
  const zip = makeZip([
    { name: "dir/", data: Buffer.alloc(0), deflate: false },
    { name: "dir/x.xml", data: Buffer.from("x"), deflate: false },
  ]);
  const out = api.unzip(zip);
  assert.equal(out.length, 1);
  assert.equal(out[0].name, "dir/x.xml");
});

test("unzip: не-ZIP → внятная ошибка", () => {
  assert.throws(() => api.unzip(Buffer.from("<html>not a zip</html>")), /EOCD/);
});

// --- разбор XML извещения ---

const SAMPLE_NOTICE = `<?xml version="1.0"?>
<ns3:export xmlns:ns5="http://zakupki.gov.ru/oos/EPtypes/1" xmlns:ns4="http://zakupki.gov.ru/oos/common/1" xmlns:ns2="http://zakupki.gov.ru/oos/base/1">
 <ns3:epNotificationEOK2020 schemeVersion="16.2">
  <ns5:commonInfo>
   <ns5:purchaseNumber>0145300010026000234</ns5:purchaseNumber>
   <ns5:plannedPublishDate>2026-08-11+03:00</ns5:plannedPublishDate>
   <ns5:href>https://zakupki.gov.ru/epz/order/notice/ok20/view/common-info.html?regNumber=0145300010026000234</ns5:href>
   <ns5:purchaseObjectInfo>Поставка немонтируемого оборудования</ns5:purchaseObjectInfo>
   <ns5:purchaseResponsibleInfo>
    <ns5:responsibleOrgInfo>
     <ns5:regNum>01453000100</ns5:regNum>
     <ns5:fullName>АДМИНИСТРАЦИЯ ЛОДЕЙНОПОЛЬСКОГО РАЙОНА</ns5:fullName>
     <ns5:INN>4711007018</ns5:INN>
     <ns5:KPP>471101001</ns5:KPP>
    </ns5:responsibleOrgInfo>
    <ns5:contactPersonInfo>
     <ns4:lastName>Тепляков</ns4:lastName>
     <ns4:firstName>Павел</ns4:firstName>
     <ns4:middleName>Михайлович</ns4:middleName>
    </ns5:contactPersonInfo>
    <ns5:contactEMail>okslp@list.ru</ns5:contactEMail>
    <ns5:contactPhone>8-81364-23978</ns5:contactPhone>
   </ns5:purchaseResponsibleInfo>
   <ns5:attachmentsInfo>
    <ns4:attachmentInfo>
     <ns4:publishedContentId>019FBE158D637A4DA93DB949F4FBB191</ns4:publishedContentId>
     <ns4:fileName>Описание объекта закупки.docx</ns4:fileName>
     <ns4:url>https://zakupki.gov.ru/44fz/filestore/public/1.0/download/priz/file.html?uid=019FBE158D63</ns4:url>
    </ns4:attachmentInfo>
    <ns4:attachmentInfo>
     <ns4:publishedContentId>019FBE1589A47DEA9F0439B8195A15BE</ns4:publishedContentId>
     <ns4:fileName>Проект сметы.xlsx</ns4:fileName>
     <ns4:url>https://zakupki.gov.ru/44fz/filestore/public/1.0/download/priz/file.html?uid=019FBE1589A4</ns4:url>
    </ns4:attachmentInfo>
   </ns5:attachmentsInfo>
  </ns5:commonInfo>
  <ns5:notificationInfo>
   <ns5:procedureInfo>
    <ns5:collectingInfo>
     <ns5:startDT>2026-08-01T19:20:29+03:00</ns5:startDT>
     <ns5:endDT>2026-08-24T09:00:00+03:00</ns5:endDT>
    </ns5:collectingInfo>
   </ns5:procedureInfo>
   <ns5:contractConditionsInfo>
    <ns5:maxPriceInfo>
     <ns5:maxPrice>612366.43</ns5:maxPrice>
     <ns5:currency><ns2:code>RUB</ns2:code></ns5:currency>
    </ns5:maxPriceInfo>
   </ns5:contractConditionsInfo>
  </ns5:notificationInfo>
 </ns3:epNotificationEOK2020>
</ns3:export>`;

test("parseNotification: ключевые поля", () => {
  const p = api.parseNotification(SAMPLE_NOTICE);
  assert.equal(p.number, "0145300010026000234");
  assert.equal(p.title, "Поставка немонтируемого оборудования");
  assert.equal(p.law, "44");
  assert.equal(p.customer, "АДМИНИСТРАЦИЯ ЛОДЕЙНОПОЛЬСКОГО РАЙОНА");
  assert.equal(p.customerInn, "4711007018");
  assert.equal(p.price, 612366.43);
  assert.equal(p.beginDate, "2026-08-01T19:20:29+03:00");
  assert.equal(p.endDate, "2026-08-24T09:00:00+03:00");
  assert.match(p.href, /regNumber=0145300010026000234/);
});

test("parseNotification: контакты 44-ФЗ (раньше только из карточки)", () => {
  const p = api.parseNotification(SAMPLE_NOTICE);
  assert.equal(p.contacts.person, "Тепляков Павел Михайлович");
  assert.equal(p.contacts.email, "okslp@list.ru");
  assert.equal(p.contacts.phone, "8-81364-23978");
});

test("parseNotification: документы с прямыми url", () => {
  const p = api.parseNotification(SAMPLE_NOTICE);
  assert.equal(p.documents.length, 2);
  assert.equal(p.documents[0].name, "Описание объекта закупки.docx");
  assert.match(p.documents[0].url, /filestore\/public/);
  assert.equal(p.documents[0].id, "019FBE158D637A4DA93DB949F4FBB191");
});

test("pickNotification: берёт последнюю версию (_N_)", () => {
  const chosen = api.pickNotification([
    { name: "epNotificationEOK2020_0145_1_AAA.xml", data: Buffer.alloc(0) },
    { name: "epNotificationEOK2020_0145_2_BBB.xml", data: Buffer.alloc(0) },
    { name: "epProtocolEOK2020Final_0145_1_CCC.xml", data: Buffer.alloc(0) },
  ]);
  assert.match(chosen.name, /_2_BBB/);
});

test("pickNotification: 223 (purchaseNotice), игнорит explanation_null", () => {
  const chosen = api.pickNotification([
    { name: "purchaseNotice_32616289305_1_AAA.xml", data: Buffer.alloc(0) },
    { name: "explanation_32616289305_null_ZZZ.xml", data: Buffer.alloc(0) },
    { name: "purchaseNotice_32616289305_3_CCC.xml", data: Buffer.alloc(0) },
    { name: "purchaseProtocol_32616289305_1_DDD.xml", data: Buffer.alloc(0) },
  ]);
  assert.match(chosen.name, /_3_CCC/);
});

// --- разбор XML извещения 223-ФЗ ---

const SAMPLE_NOTICE_223 = `<?xml version="1.0"?>
<ns2:purchaseNotice xmlns="http://zakupki.gov.ru/223fz/types/1" xmlns:ns2="http://zakupki.gov.ru/223fz/purchase/1">
 <body><item><purchaseNoticeData>
  <registrationNumber>32616289305</registrationNumber>
  <name>Поставка Свинца ССу2</name>
  <urlEIS>https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber=32616289305</urlEIS>
  <customer>
   <mainInfo>
    <fullName>АО "ННИИММ "ПРОМЕТЕЙ"</fullName>
    <shortName>АО "ННИИММ "ПРОМЕТЕЙ"</shortName>
    <inn>5263004406</inn>
    <kpp>526301001</kpp>
    <region>НИЖЕГОРОДСКАЯ ОБЛАСТЬ</region>
    <legalAddress>603003, НИЖЕГОРОДСКАЯ ОБЛАСТЬ, Г. НИЖНИЙ НОВГОРОД</legalAddress>
    <placer><contact>
     <lastName>Елистратов</lastName><firstName>Н.</firstName><middleName>В.</middleName>
     <phone>+8 (831) 2730375</phone><email>omtsp@yandex.ru</email>
    </contact></placer>
   </mainInfo>
  </customer>
  <publicationDateTime>2026-08-13T10:00:00+03:00</publicationDateTime>
  <submissionCloseDateTime>2026-08-25T09:00:00+03:00</submissionCloseDateTime>
  <lots><lot><lotData><initialSum>5050000</initialSum><currency><code>RUB</code></currency></lotData></lot></lots>
  <attachments>
   <totalDocumentsCount>2</totalDocumentsCount>
   <document>
    <fileName>Техническое задание.xlsx</fileName>
    <description>ТЗ</description>
    <url>https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=A8F042F0CFDF4779BE03A7BF4D2C314D</url>
   </document>
   <document>
    <fileName>Проект договора.docx</fileName>
    <url>https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=A60BF36AF6014AAB</url>
   </document>
  </attachments>
 </purchaseNoticeData></item></body>
</ns2:purchaseNotice>`;

test("parseNotification223: ключевые поля 223", () => {
  const p = api.parseNotification223(SAMPLE_NOTICE_223);
  assert.equal(p.number, "32616289305");
  assert.equal(p.title, "Поставка Свинца ССу2");
  assert.equal(p.law, "223");
  assert.equal(p.customer, 'АО "ННИИММ "ПРОМЕТЕЙ"');
  assert.equal(p.customerInn, "5263004406");
  assert.equal(p.region, "НИЖЕГОРОДСКАЯ ОБЛАСТЬ");
  assert.equal(p.price, 5050000);
  assert.match(p.href, /regNumber=32616289305/);
  assert.equal(p.endDate, "2026-08-25T09:00:00+03:00");
});

test("parseNotification223: контакты и документы 223", () => {
  const p = api.parseNotification223(SAMPLE_NOTICE_223);
  assert.equal(p.contacts.person, "Елистратов Н. В.");
  assert.equal(p.contacts.email, "omtsp@yandex.ru");
  assert.equal(p.contacts.phone, "+8 (831) 2730375");
  assert.equal(p.documents.length, 2);
  assert.equal(p.documents[0].name, "Техническое задание.xlsx");
  assert.match(p.documents[0].url, /223\/filestore/);
  assert.equal(p.documents[0].id, "A8F042F0CFDF4779BE03A7BF4D2C314D");
});

test("parseNotice: диспетчер по корню (44 vs 223)", () => {
  const p223 = api.parseNotice("purchaseNotice_326_3_X.xml", SAMPLE_NOTICE_223);
  assert.equal(p223.law, "223");
  assert.equal(p223.number, "32616289305");
  const p44 = api.parseNotice("epNotificationEOK2020_0145_2_Y.xml", SAMPLE_NOTICE);
  assert.equal(p44.law, "44");
  assert.equal(p44.number, "0145300010026000234");
});

test("tunnelize: разбирает абсолютный archiveUrl", () => {
  const t = api.tunnelize("https://int.zakupki.gov.ru/dstore/common/download/compound?docRequestUid=X&compoundUid=Y");
  assert.equal(t.host, "int.zakupki.gov.ru");
  assert.equal(t.path, "/dstore/common/download/compound?docRequestUid=X&compoundUid=Y");
});
