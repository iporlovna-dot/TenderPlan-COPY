// Тесты разбора ТЗ в связке с накопителем: node tools/test_tzterms.js
//
// Проверяем то, что появилось вместе с накопителем: запись живёт неделями и
// приходит в annotateTz уже с терминами прошлых прогонов. Ошибиться здесь можно
// молча — статус разъедется с содержимым, и закупка с готовым разбором будет
// выглядеть как «ждёт очереди».
//
// Сеть не трогаем: при budget = 0 очередь скачивания пуста.

const test = require("node:test");
const assert = require("node:assert");

const {
  annotateTz, hasTerms, unparsedFirst, rankTzDocs, isHopeless, termsForPurchase, applyTz,
} = require("./sources/tzterms");
const LKTZ = require("../site/js/tzmatch.js");
const { extractLotItemsFromTables, xlsxSheetsToTables } = require("./sources/ktrutable");

// Минимальный ZIP из STORED-записей (метод 0, без сжатия) — читается тем же
// разбором центрального каталога, что и настоящий .xlsx. Позволяет проверить
// извлечение текста из Excel без бинарной фикстуры и без сети.
function zipStored(files) {
  const parts = [], central = [];
  let off = 0;
  for (const f of files) {
    const nm = Buffer.from(f.name, "utf8"), data = f.data;
    const lh = Buffer.alloc(30);
    lh.writeUInt32LE(0x04034b50, 0); lh.writeUInt16LE(20, 4);
    lh.writeUInt32LE(data.length, 18); lh.writeUInt32LE(data.length, 22);
    lh.writeUInt16LE(nm.length, 26);
    const localOff = off;
    parts.push(lh, nm, data); off += 30 + nm.length + data.length;
    const ch = Buffer.alloc(46);
    ch.writeUInt32LE(0x02014b50, 0); ch.writeUInt16LE(20, 4); ch.writeUInt16LE(20, 6);
    ch.writeUInt32LE(data.length, 20); ch.writeUInt32LE(data.length, 24);
    ch.writeUInt16LE(nm.length, 28); ch.writeUInt32LE(localOff, 42);
    central.push(ch, nm);
  }
  const cd = Buffer.concat(central), cdOff = off;
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(files.length, 8); eocd.writeUInt16LE(files.length, 10);
  eocd.writeUInt32LE(cd.length, 12); eocd.writeUInt32LE(cdOff, 16);
  return Buffer.concat([...parts, cd, eocd]);
}

const DOC = { id: "d1", name: "Техническое задание.docx", url: "https://example.invalid/tz.docx" };

function purchase(id, over = {}) {
  return { id, number: id.replace(/^\w+_/, ""), documents: [DOC], ...over };
}

test("разобранная запись не понижается до «ждёт очереди»", async () => {
  const p = purchase("eis_1", { tzTerms: ["перчатк", "нитрилов"], tzStatus: "ok" });
  await annotateTz([p], {}, 0);
  assert.equal(p.tzStatus, "ok", "иначе готовый разбор выглядит как необработанный");
  assert.deepEqual(p.tzTerms, ["перчатк", "нитрилов"]);
});

test("неразобранная запись честно помечается «ждёт очереди»", async () => {
  const p = purchase("eis_2");
  await annotateTz([p], {}, 0);
  assert.equal(p.tzStatus, "pending");
});

test("закупка с терминами, но без документов, не объявляется «без документов»", async () => {
  // так бывает после слияния: документы в окне не пришли, а термины накоплены
  const p = purchase("eis_3", { documents: [], tzTerms: ["перчатк"], tzStatus: "ok" });
  await annotateTz([p], {}, 0);
  assert.equal(p.tzStatus, "ok");
});

test("без документов и без терминов — статус зависит от того, заходили ли в карточку", async () => {
  const seen = purchase("eis_4", { documents: [], docsFetched: true });
  const unseen = purchase("eis_5", { documents: [], docsFetched: false });
  await annotateTz([seen, unseen], {}, 0);
  assert.equal(seen.tzStatus, "no-doc", "в карточку заходили — приложений действительно нет");
  assert.equal(unseen.tzStatus, "pending", "в карточку не заходили — это наша недоработка, а не свойство закупки");
});

test("статус чинится по факту наличия терминов", async () => {
  // так выглядят записи после прогона, который успел пометить разобранные как pending
  const p = purchase("eis_7", { tzTerms: ["перчатк"], tzStatus: "pending" });
  await annotateTz([p], {}, 0);
  assert.equal(p.tzStatus, "ok", "термины есть — значит разбор удался, и запись не должна врать");
});

test("кэш применяется и переопределяет накопленное", async () => {
  const p = purchase("eis_6", { tzTerms: ["старое"], tzStatus: "ok" });
  await annotateTz([p], { eis_6: { terms: ["новое"], docName: "ТЗ.docx", status: "ok" } }, 0);
  assert.deepEqual(p.tzTerms, ["новое"]);
  assert.equal(p.tzDoc, "ТЗ.docx");
});

test("в очередь разбора первыми идут ни разу не разобранные", () => {
  const need = [
    { p: purchase("eis_parsed_near", { tzTerms: ["a"] }) },   // ближе дедлайн, но уже разобрана
    { p: purchase("eis_new_far") },
    { p: purchase("eis_parsed_far", { tzTerms: ["b"] }) },
    { p: purchase("eis_new_farther") },
  ];
  const order = need.slice().sort(unparsedFirst).map(x => x.p.id);
  assert.deepEqual(order, ["eis_new_far", "eis_new_farther", "eis_parsed_near", "eis_parsed_far"],
    "внутри групп порядок по дедлайну обязан сохраниться — сортировка стабильна");
});

test("hasTerms не считает пустой массив разбором", () => {
  assert.equal(hasTerms({ tzTerms: [] }), false);
  assert.equal(hasTerms({}), false);
  assert.equal(hasTerms({ tzTerms: ["перчатк"] }), true);
});

// ------------------------------------------------ спецификация из таблицы КТРУ

test("запись без отметки версии извлечения не считается разобранной", () => {
  // Живой прогон 14.08: метка __items переоткрыла 1868 разборов, и 1848 из них
  // тут же вернулись из накопителя БЕЗ спецификации — засев смотрел только на
  // tzAlgo. Они застряли бы навсегда: перекачивать их больше нечему.
  const TZ_ALGO = 4, TZ_ITEMS = 2;
  const FINAL = new Set(["ok", "unsupported", "empty", "error"]);
  const seedable = (p) => p.tzAlgo === TZ_ALGO && p.tzItems === TZ_ITEMS && FINAL.has(p.tzStatus);
  assert.equal(seedable({ tzAlgo: 4, tzStatus: "ok" }), false, "старая запись без tzItems");
  assert.equal(seedable({ tzAlgo: 4, tzItems: 1, tzStatus: "ok" }), false, "прошлая версия извлечения");
  assert.equal(seedable({ tzAlgo: 4, tzItems: 2, tzStatus: "ok" }), true);
  assert.equal(seedable({ tzAlgo: 4, tzItems: 2, tzStatus: "ok", lotItems: [] }), true,
    "разобрали текущей версией и таблицы не нашли — это ответ, а не пробел");
});

test("позиции лота доезжают до закупки", () => {
  const p = purchase("eis_10");
  const items = [{ name: "Перчатки", ktru: "32.50.13.190-00007686", chars: [] }];
  applyTz(p, { status: "ok", docName: "ТЗ.docx", terms: ["перчатк"], items });
  assert.deepEqual(p.lotItems, items);
});

test("пустая спецификация полем не становится", () => {
  const p = purchase("eis_11");
  applyTz(p, { status: "ok", docName: "ТЗ.docx", terms: ["перчатк"], items: [] });
  assert.equal("lotItems" in p, false,
    "«таблицы в документе нет» и «мы её не извлекали» — разные вещи");
});

test("разбор без спецификации не стирает добытую раньше", () => {
  // так бывает при перезаписи из кэша старого формата
  const p = purchase("eis_12", { lotItems: [{ name: "Бинт", chars: [] }] });
  applyTz(p, { status: "ok", docName: "ТЗ.docx", terms: ["бинт"] });
  assert.equal(p.lotItems.length, 1, "дорого добытое затирать нечем");
});

// ------------------------------------------- перебор кандидатов вместо одного

const doc = (name) => ({ id: name, name, url: "https://example.invalid/" + name });

test("заведомо неразбираемые форматы опознаются по имени", () => {
  // .xls (старый бинарный) остаётся безнадёжным, .xlsx (Open XML) — нет
  for (const n of ["ТЗ.pdf", "смета.xls", "проект.doc", "прил.rar", "скан.JPEG", "подпись.sig", "арх.zip"]) {
    assert.equal(isHopeless(n), true, n);
  }
  for (const n of ["ТЗ.docx", "спец.xlsx", "Приложение 1", "техзадание", "файл.DOCX", "таблица.XLSX"]) {
    assert.equal(isHopeless(n), false, n);
  }
});

test("кандидаты: .xlsx разбираем, но .docx предпочитаем при равном имени", () => {
  const got = rankTzDocs([
    doc("Техническое задание.xlsx"), doc("Обоснование НМЦК.pdf"), doc("Проект контракта.docx"),
  ]).map(d => d.name);
  assert.ok(got.includes("Техническое задание.xlsx"), "xlsx не отбрасываем");
  assert.ok(!got.includes("Обоснование НМЦК.pdf"), "pdf по-прежнему безнадёжен");
  const eq = rankTzDocs([doc("Спецификация.xlsx"), doc("Спецификация.docx")]).map(d => d.name);
  assert.equal(eq[0], "Спецификация.docx", "при равных именах .docx выше .xlsx");
});

test("xlsxTextFromBytes: текст из sharedStrings + число с единицей", async () => {
  const shared = `<?xml version="1.0"?><sst><si><t>Перчатки нитриловые</t></si>`
    + `<si><r><t>0,1 </t></r><r><t>мм</t></r></si></sst>`;
  const sheet = `<worksheet><sheetData>`
    + `<row><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>`
    + `<row><c r="A2" t="inlineStr"><is><t>ГОСТ 1292</t></is></c></row>`
    + `</sheetData></worksheet>`;
  const zip = zipStored([
    { name: "xl/sharedStrings.xml", data: Buffer.from(shared, "utf8") },
    { name: "xl/worksheets/sheet1.xml", data: Buffer.from(sheet, "utf8") },
  ]);
  const text = await LKTZ.xlsxTextFromBytes(new Uint8Array(zip));
  assert.match(text, /Перчатки нитриловые/);
  assert.match(text, /0,1 мм/, "rich-text runs склеены, единица рядом с числом");
  assert.match(text, /ГОСТ 1292/, "inline-строка прочитана");
  const freq = LKTZ.termFreq(text);
  assert.ok([...freq.keys()].some(t => t.startsWith("перчатк")), "предмет извлечён");
  assert.ok(freq.has("0,1 мм"), "числовое требование извлечено");
});

test("xlsxTextFromBytes: не-xlsx ZIP → ошибка (падаем на unsupported)", async () => {
  const zip = zipStored([{ name: "word/document.xml", data: Buffer.from("<x/>", "utf8") }]);
  await assert.rejects(() => LKTZ.xlsxTextFromBytes(new Uint8Array(zip)), /не \.xlsx/i);
});

test("xlsx→спецификация: шапка под пустыми строками, пропуск колонки, requirements", async () => {
  // Сквозной офлайн-путь: строки листа с ПРОПУЩЕННОЙ колонкой C (проверка позиций
  // по r="B3") и пустыми строками сверху → те же парсеры таблиц.
  const sst = `<?xml version="1.0"?><sst>`
    + `<si><t>Наименование товара</t></si>`         // 0
    + `<si><t>Нормативно-технические требования</t></si>` // 1
    + `<si><t>Единица измерения</t></si>`            // 2
    + `<si><t>Объем</t></si>`                        // 3
    + `<si><t>Свинец ССу2</t></si>`                  // 4
    + `<si><t>ГОСТ 1292-81, сурьма не менее 2,8 %</t></si>` // 5
    + `<si><t>т</t></si>`                            // 6
    + `<si><t>Приложение № 2</t></si></sst>`;        // 7
  // A,B заняты, C пропущена, D,E заняты — если колонки поедут, unit/qty встанут не туда
  const sheet = `<?xml version="1.0"?><worksheet><sheetData>`
    + `<row r="1"><c r="M1" t="s"><v>7</v></c></row>`
    + `<row r="2"></row><row r="3"></row><row r="4"></row>`
    + `<row r="5"><c r="A5" t="s"><v>0</v></c><c r="B5" t="s"><v>1</v></c>`
    + `<c r="D5" t="s"><v>2</v></c><c r="E5" t="s"><v>3</v></c></row>`
    + `<row r="6"><c r="A6" t="s"><v>4</v></c><c r="B6" t="s"><v>5</v></c>`
    + `<c r="D6" t="s"><v>6</v></c><c r="E6"><v>20</v></c></row>`
    + `</sheetData></worksheet>`;
  const zip = zipStored([
    { name: "xl/sharedStrings.xml", data: Buffer.from(sst, "utf8") },
    { name: "xl/worksheets/sheet1.xml", data: Buffer.from(sheet, "utf8") },
  ]);
  const sheets = await LKTZ.xlsxSheetsFromBytes(new Uint8Array(zip));
  const res = extractLotItemsFromTables(xlsxSheetsToTables(sheets));
  assert.equal(res.status, "goods");
  assert.equal(res.items.length, 1);
  const it = res.items[0];
  assert.equal(it.name, "Свинец ССу2");
  assert.equal(it.unit, "т", "колонка D не съехала из-за пропущенной C");
  assert.equal(it.qty, 20, "«Объем» (E) — количество");
  assert.equal(it.chars.length, 1);
  assert.match(it.chars[0].value, /2,8 %/);
});

test("кандидаты: безнадёжные отброшены, похожие на ТЗ впереди", () => {
  const got = rankTzDocs([
    doc("Проект контракта.docx"), doc("Обоснование НМЦК.pdf"),
    doc("Техническое задание.docx"), doc("Приложение 2"),
  ]).map(d => d.name);
  assert.equal(got[0], "Техническое задание.docx", "имя решает");
  assert.ok(!got.includes("Обоснование НМЦК.pdf"), "pdf качать незачем — ответ известен заранее");
  assert.equal(got.length, 3);
});

test("документ без расширения остаётся кандидатом", () => {
  // у ЕИС половина документов без расширения, и среди них полно настоящих .docx
  assert.deepEqual(rankTzDocs([doc("Приложение 1")]).map(d => d.name), ["Приложение 1"]);
});

test("перебор доходит до годного документа", async () => {
  const seen = [];
  const parse = async (d) => {
    seen.push(d.name);
    return d.name === "третий"
      ? { terms: ["перчатк"], docName: d.name, status: "ok" }
      : { terms: [], docName: d.name, status: "unsupported" };
  };
  const res = await termsForPurchase([doc("первый"), doc("второй"), doc("третий")], () => true, parse);
  assert.equal(res.status, "ok");
  assert.equal(res.spent, 3, "три скачивания — столько и списать с бюджета");
  assert.deepEqual(seen, ["первый", "второй", "третий"]);
});

test("годный документ найден сразу — лишнего не качаем", async () => {
  let calls = 0;
  const parse = async (d) => (calls++, { terms: ["перчатк"], docName: d.name, status: "ok" });
  const res = await termsForPurchase([doc("а"), doc("б"), doc("в")], () => true, parse);
  assert.equal(calls, 1);
  assert.equal(res.spent, 1);
});

test("все кандидаты плохи — возвращается их вердикт, а не «ждёт очереди»", async () => {
  const parse = async (d) => ({ terms: [], docName: d.name, status: "unsupported" });
  const res = await termsForPurchase([doc("а"), doc("б")], () => true, parse);
  assert.equal(res.status, "unsupported", "это окончательный ответ, его можно кэшировать");
});

test("бюджет кончился на середине — «ждёт очереди», а не ложный вердикт", async () => {
  let left = 1;
  const spend = () => (left > 0 ? (left--, true) : false);
  const parse = async (d) => ({ terms: [], docName: d.name, status: "unsupported" });
  const res = await termsForPurchase([doc("а"), doc("б"), doc("в")], spend, parse);
  assert.equal(res.status, "pending",
    "иначе закэшируем «формат не тот», не дочитав остальные, и закроем закупке дорогу навсегда");
});

test("приложения есть, но все нечитаемые — вердикт без единого скачивания", async () => {
  const p = { id: "eis_9", number: "9", docsFetched: true,
              documents: [doc("ТЗ.pdf"), doc("смета.xls")] };
  await annotateTz([p], {}, 0);   // бюджет 0: качать нечем, и не понадобится
  assert.equal(p.tzStatus, "unsupported");
});
