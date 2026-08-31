// Тесты извлечения позиций из таблицы КТРУ: node --test tools/test_ktrutable.js
//
// Всё здесь ошибается молча. Не та таблица — и вместо спецификации разберём
// список инвентаря; съехавшая колонка — и характеристика встанет на место
// товара; потерянный перенос позиции — и 24 характеристики одного товара
// превратятся в 24 товара без характеристик. Сеть не трогаем: XML синтетический.

const test = require("node:test");
const assert = require("node:assert");

const {
  docxTables, mapColumns, isSpecHeader, pickSpecTable,
  parseValue, parseHardness, parseSpecTable, extractLotItems, extractLotItemsFromTables,
  xlsxSheetsToTables, pickGoodsTable, parseGoodsTable, isGoodsName, isEnumerationRow,
} = require("./sources/ktrutable");

// ─────────────────────────────────────────────────────── сборка тестового XML

const t = (s) => `<w:p><w:r><w:t>${s}</w:t></w:r></w:p>`;
const tc = (s) => `<w:tc><w:tcPr/>${t(s)}</w:tc>`;
const tcSpan = (s, n) => `<w:tc><w:tcPr><w:gridSpan w:val="${n}"/></w:tcPr>${t(s)}</w:tc>`;
const tr = (cells) => `<w:tr>${cells.map(tc).join("")}</w:tr>`;
const tbl = (rows) => `<w:tbl><w:tblPr/>${rows.map(tr).join("")}</w:tbl>`;

// ────────────────────────────────────────────────────────────── разбор таблиц

test("ячейки и строки не слипаются", () => {
  const tables = docxTables(tbl([["Объем", "≥ 5"], ["Длина", "4"]]));
  assert.equal(tables.length, 1);
  assert.deepEqual(tables[0].rows, [["Объем", "≥ 5"], ["Длина", "4"]]);
});

test("вложенная таблица не схлопывает внешнюю", () => {
  // ровно то, на чём ломается /<w:tbl>[\s\S]*?<\/w:tbl>/
  const inner = tbl([["внутр"]]);
  const xml = `<w:tbl><w:tr><w:tc>${inner}</w:tc></w:tr><w:tr>${tc("снаружи")}</w:tr></w:tbl>`;
  const tables = docxTables(xml);
  assert.equal(tables.length, 2, "должны найтись обе, а не одна обрезанная");
  assert.ok(tables.some((x) => x.rows.some((r) => r.includes("снаружи"))),
    "строка после вложенной таблицы обязана остаться во внешней");
});

test("Word рвёт слово на runs — склеиваем без пробела", () => {
  const xml = `<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Перча</w:t></w:r><w:r><w:t>тки</w:t></w:r></w:p></w:tc></w:tr></w:tbl>`;
  assert.deepEqual(docxTables(xml)[0].rows, [["Перчатки"]]);
});

test("текст вне таблицы в строки не попадает", () => {
  assert.deepEqual(docxTables(`${t("преамбула")}${tbl([["А"]])}`)[0].rows, [["А"]]);
});

// ──────────────────────────────────────────────────────────── выбор колонок

test("«наименование характеристики» не занимает колонку товара", () => {
  const map = mapColumns(["№ п/п", "Наименование товара", "Код ОКПД 2 / КТРУ",
                          "Наименование характеристики", "Значение характеристики", "Ед. изм."]);
  assert.equal(map.name, 1, "колонка товара");
  assert.equal(map.charName, 3, "иначе характеристика встанет на место товара");
  assert.equal(map.charValue, 4);
  assert.equal(map.code, 2);
  assert.equal(map.unit, 5);
});

test("заголовки другого региона тоже опознаются", () => {
  const map = mapColumns(["№ п/п", "Наименование товара по КТРУ /Наименование товара",
                          "Требуемый параметр", "Требуемое значение", "Кол-во", "Ед. изм."]);
  assert.ok(isSpecHeader(map));
  assert.equal(map.charName, 2);
  assert.equal(map.charValue, 3);
  assert.equal(map.qty, 4);
});

test("список инвентаря спецификацией не считается", () => {
  // настоящая таблица из живого ТЗ: 682 строки, больше всех остальных
  const map = mapColumns(["№ п/п", "Наименование оборудования и инструментов",
                          "модель", "инв №", "зав. №", "Периодичность ТО"]);
  assert.equal(isSpecHeader(map), false,
    "иначе разберём список техники для обслуживания вместо спецификации");
});

test("самая длинная таблица не выигрывает, если она не спецификация", () => {
  const junk = tbl([["№ п/п", "Наименование оборудования и инструментов", "инв №"],
                    ...Array.from({ length: 30 }, (_, i) => [String(i), "станок", "инв-" + i])]);
  const spec = tbl([["Наименование товара", "Код КТРУ", "Наименование характеристики", "Значение характеристики"],
                    ["Перчатки", "32.50.13.190-00007686", "Размер", "≥ 8"]]);
  const picked = pickSpecTable(docxTables(junk + spec));
  assert.equal(picked.body.length, 1);
  assert.equal(picked.body[0][0], "Перчатки");
});

test("gridSpan раскрывается — объединённая ячейка не съедает соседние колонки", () => {
  const tables = docxTables(tbl([["А", "Б"]]));
  assert.deepEqual(tables, [{ rows: [["А", "Б"]] }]);
  // <w:tc> с gridSpan=3 обязана превратиться в три ячейки с тем же текстом —
  // иначе колонки под объединённой шапкой съезжают относительно строк данных.
  const xml = `<w:tbl><w:tr>${tc("№")}${tcSpan("Широкая", 3)}${tc("Хвост")}</w:tr></w:tbl>`;
  assert.deepEqual(docxTables(xml)[0].rows, [["№", "Широкая", "Широкая", "Широкая", "Хвост"]]);
});

test("двухуровневая шапка (gridSpan сверху, разведение снизу) даёт charValue", () => {
  // реальный формат живого 44-ФЗ ТЗ: верхняя строка объединяет charName/
  // charValue/ед.изм/инструкцию в одну ячейку «Характеристики товара», а
  // делит их по колонкам строка ниже. До фикса charValue терялась целиком —
  // верхняя шапка находила только charName, а строка-разводка не
  // подхватывалась вовсе (spec.json отдавал chars:[] для реальной позиции).
  const xml = `<w:tbl>` +
    `<w:tr>${tc("№ п/п")}${tc("Наименование товара")}${tc("Код КТРУ")}` +
      `${tcSpan("Характеристики товара", 2)}${tc("Ед. изм.")}${tc("Кол-во")}</w:tr>` +
    `<w:tr>${tc("")}${tc("")}${tc("")}${tc("Наименование характеристики")}` +
      `${tc("Значение характеристики")}${tc("")}${tc("")}</w:tr>` +
    `<w:tr>${tc("1")}${tc("Перчатки")}${tc("32.50.13.190-00007686")}` +
      `${tc("Размер")}${tc("≥ 8")}${tc("шт")}${tc("10")}</w:tr>` +
    `</w:tbl>`;
  const [it] = parseSpecTable(pickSpecTable(docxTables(xml)));
  assert.equal(it.name, "Перчатки");
  assert.equal(it.ktru, "32.50.13.190-00007686");
  assert.equal(it.qty, 10, "количество должно читаться из реальной колонки, а не съехавшей");
  assert.equal(it.unit, "шт");
  assert.equal(it.chars.length, 1, "без доразбора шапки characteristics оставались бы пустыми");
  assert.equal(it.chars[0].key, "Размер");
  assert.deepEqual(it.chars[0].value, 8);
});

test("шапка ниже объединённого титула тоже находится", () => {
  const x = tbl([["Описание объекта закупки"],
                 ["Наименование товара", "Код КТРУ", "Наименование характеристики", "Значение характеристики"],
                 ["Бинт", "21.20.23.110-00004254", "Длина", "не менее 5"]]);
  const picked = pickSpecTable(docxTables(x));
  assert.equal(picked.headerRow, 1);
  assert.equal(picked.body[0][0], "Бинт");
});

// ──────────────────────────────────────────────────────── значения и операторы

test("операторы словами — как их пишет заказчик", () => {
  assert.deepEqual(parseValue("не менее или равно 4"), { operator: "gte", value: 4 });
  assert.deepEqual(parseValue("не более или равно 2"), { operator: "lte", value: 2 });
  assert.deepEqual(parseValue("не менее 0,25"), { operator: "gte", value: 0.25 },
    "десятичная запятая — русская запись, точки в ТЗ почти не бывает");
});

test("операторы знаками", () => {
  assert.deepEqual(parseValue("≥ 5"), { operator: "gte", value: 5 });
  assert.deepEqual(parseValue("≤ 120"), { operator: "lte", value: 120 });
});

test("диапазон разбирается раньше одиночных операторов", () => {
  assert.deepEqual(parseValue("≥ 115 и ≤ 120"), { operator: "range", value: [115, 120] },
    "иначе останется только первая граница и требование станет вдвое шире");
  assert.deepEqual(parseValue("от 5 до 10"), { operator: "range", value: [5, 10] });
});

test("«соответствие» — требование есть, числа нет", () => {
  assert.deepEqual(parseValue("соответствие"), { operator: "present", value: true });
  assert.deepEqual(parseValue("наличие"), { operator: "present", value: true });
});

test("хвостовая пунктуация не превращает «соответствие» в текст", () => {
  // в этих таблицах характеристики перечисляют через «;» — голое «соответствие»
  // встречается реже, чем «Соответствие;»
  assert.deepEqual(parseValue("Соответствие;"), { operator: "present", value: true });
  assert.deepEqual(parseValue("соответствие."), { operator: "present", value: true });
});

test("текстовое значение сохраняется как есть", () => {
  assert.deepEqual(parseValue("деревянный брус хвойных пород"),
    { operator: "eq", value: "деревянный брус хвойных пород" });
});

test("голое число — точное равенство", () => {
  assert.deepEqual(parseValue("128"), { operator: "eq", value: 128 });
});

test("пустая ячейка требованием не становится", () => {
  assert.equal(parseValue(""), null);
  assert.equal(parseValue("   "), null);
});

test("hard и soft читаются из инструкции дословно", () => {
  assert.equal(parseHardness("Значение характеристики не может изменяться участником закупки"), "hard");
  assert.equal(parseHardness("Участник закупки указывает в заявке конкретное значение"), "soft");
  assert.equal(parseHardness(""), "soft", "по умолчанию не дисквалифицируем");
});

// ───────────────────────────────────────────────────────── позиции целиком

const REAL = tbl([
  ["№ п/п", "Наименование товара", "Код ОКПД 2 / КТРУ", "Наименование характеристики",
   "Значение характеристики", "Ед. изм.", "Кол-во", "Инструкция по заполнению заявки"],
  ["1", "Кабель соединительный", "32.50.50.190-00000123", "Длина", "не менее или равно 4", "м", "6",
   "Участник закупки указывает в заявке конкретное значение"],
  ["", "", "", "Количество проводников", "не более или равно 2", "шт", "",
   "Участник закупки указывает в заявке конкретное значение"],
  ["", "", "", "Цельнолитые штекеры", "соответствие", "", "",
   "Значение характеристики не может изменяться участником закупки"],
  ["2", "Перчатки нитриловые", "32.50.13.190-00007686", "Объем", "≥ 115 и ≤ 120",
   "Кубический сантиметр; миллилитр", "50", ""],
]);

test("одна позиция на много строк — объединённые ячейки не рвут товар", () => {
  const items = parseSpecTable(pickSpecTable(docxTables(REAL)));
  assert.equal(items.length, 2, "иначе 3 характеристики кабеля станут 3 товарами");
  assert.equal(items[0].name, "Кабель соединительный");
  assert.equal(items[0].chars.length, 3, "все характеристики принадлежат первой позиции");
  assert.equal(items[1].name, "Перчатки нитриловые");
});

test("код, количество и единица достаются из строки позиции", () => {
  const [cable, gloves] = parseSpecTable(pickSpecTable(docxTables(REAL)));
  assert.equal(cable.ktru, "32.50.50.190-00000123");
  assert.equal(cable.okpd, "32.50.50.190", "ОКПД2-часть нужна для градации group в ktru.py");
  assert.equal(cable.qty, 6);
  assert.equal(gloves.qty, 50);
  assert.equal(gloves.unit, "Кубический сантиметр; миллилитр");
});

test("характеристика несёт оператор, единицу и жёсткость", () => {
  const [cable] = parseSpecTable(pickSpecTable(docxTables(REAL)));
  assert.deepEqual(cable.chars[0], {
    key: "Длина", operator: "gte", value: 4, unit: "м", hardness: "soft",
    raw: "не менее или равно 4",
  });
  assert.equal(cable.chars[2].operator, "present");
  assert.equal(cable.chars[2].hardness, "hard", "«не может изменяться» дисквалифицирует");
});

test("диапазон доезжает до позиции целиком", () => {
  const [, gloves] = parseSpecTable(pickSpecTable(docxTables(REAL)));
  assert.deepEqual(gloves.chars[0].value, [115, 120]);
  assert.equal(gloves.chars[0].operator, "range");
});

test("код КТРУ находится, даже когда он не в своей колонке", () => {
  // часть заказчиков пишет код прямо в характеристиках, отдельной колонки нет
  const x = tbl([
    ["№", "Наименование товара", "Требуемый параметр", "Требуемое значение"],
    ["1", "Кабель соединительный", "Код по КТРУ: 32.50.50.190-00000123", ""],
    ["", "", "Длина", "не менее 4"],
  ]);
  const [it] = parseSpecTable(pickSpecTable(docxTables(x)));
  assert.equal(it.ktru, "32.50.50.190-00000123");
  assert.equal(it.okpd, "32.50.50.190");
});

test("нумерация характеристики не становится частью её имени", () => {
  const x = tbl([
    ["Наименование товара", "Код КТРУ", "Наименование характеристики", "Значение характеристики"],
    ["Набор реагентов", "21.20.23.110-00005395", "2. Назначение", "для анализаторов"],
  ]);
  const [it] = parseSpecTable(pickSpecTable(docxTables(x)));
  assert.equal(it.chars[0].key, "Назначение");
});

test("единица не липнет к текстовому значению", () => {
  // объединённая ячейка тянет единицу ТОВАРА вниз по всем строкам
  const x = tbl([
    ["Наименование товара", "Код КТРУ", "Наименование характеристики", "Значение характеристики", "Ед. изм."],
    ["Кювета", "32.50.50.190-00002993", "Назначение", "Для анализаторов серии Cobas", "Штука"],
    ["", "", "Количество кювет в кассете", "не менее 400", "Штука"],
  ]);
  const [it] = parseSpecTable(pickSpecTable(docxTables(x)));
  assert.equal(it.chars[0].unit, "", "«Назначение = текст, Штука» — мусор");
  assert.equal(it.chars[1].unit, "Штука", "у числового сравнения единица осмысленна");
});

test("строки «Итого» и заголовки разделов в позиции не попадают", () => {
  const x = tbl([
    ["Наименование товара", "Код КТРУ", "Наименование характеристики", "Значение характеристики"],
    ["Раздел 1. Расходные материалы", "", "", ""],
    ["Бинт", "21.20.23.110-00004254", "Длина", "не менее 5"],
    ["Итого", "", "", ""],
  ]);
  const items = parseSpecTable(pickSpecTable(docxTables(x)));
  assert.deepEqual(items.map((i) => i.name), ["Бинт"],
    "без кода и без характеристик это не товар");
});

test("документ без таблиц отвечает статусом, а не пустотой", () => {
  assert.deepEqual(extractLotItems(t("просто текст")), { items: [], status: "no-table" });
});

test("таблица есть, а спецификации нет — тоже отдельный статус", () => {
  const junk = tbl([["Наименование оборудования и инструментов", "инв №"], ["станок", "1"]]);
  assert.equal(extractLotItems(junk).status, "no-table");
});

// ─────────────────────────────────────── B: пропуск нумерации и комбинированная шапка

test("строка нумерации столбцов не крадёт характеристики следующей позиции", () => {
  const x = tbl([
    ["Наименование товара", "Код КТРУ", "Наименование характеристики", "Значение характеристики"],
    ["1", "2", "3", "4"],                        // строка нумерации колонок
    ["Бинт", "21.20.23.110-00004254", "Длина", "не менее 5"],
  ]);
  const items = parseSpecTable(pickSpecTable(docxTables(x)));
  assert.deepEqual(items.map((i) => i.name), ["Бинт"], "нумерация не должна стать позицией «1»");
  assert.equal(items[0].chars.length, 1, "характеристика принадлежит Бинту, а не строке нумерации");
});

test("isEnumerationRow: только последовательность разных коротких чисел", () => {
  assert.equal(isEnumerationRow(["1", "2", "3", "4"]), true);
  assert.equal(isEnumerationRow(["1", "Бинт", "3"]), false, "есть текст — не нумерация");
  assert.equal(isEnumerationRow(["5", "5", "5"]), false, "повторы — это данные, не нумерация");
});

test("комбинированная шапка «Наименование товара / … / количество» — это товар, не qty", () => {
  const map = mapColumns(["№ п/п", "Наименование товара / номер позиции в КТРУ / количество",
                          "Код КТРУ", "Наименование характеристики", "Значение характеристики"]);
  assert.equal(map.name, 1, "иначе слово «количество» уводит колонку в qty и таблица теряет товар");
  assert.ok(isSpecHeader(map));
});

// ─────────────────────────────────────── A: товарная таблица без КТРУ (фолбэк)

test("товарная таблица без КТРУ — берём реальные названия как позиции без характеристик", () => {
  const x = tbl([
    ["№", "Наименование товара", "Ед. изм.", "Кол-во"],
    ["1", "Бинт эластичный медицинский", "шт", "10"],
    ["2", "Салфетки бумажные", "упак", "5"],
  ]);
  const res = extractLotItems(x);
  assert.equal(res.status, "goods");
  assert.deepEqual(res.items.map((i) => i.name), ["Бинт эластичный медицинский", "Салфетки бумажные"]);
  assert.equal(res.items[0].qty, 10);
  assert.equal(res.items[0].chars.length, 0, "у товарной таблицы характеристик нет");
});

test("товарная таблица отсеивает итоги и финансовые строки", () => {
  const x = tbl([
    ["Наименование", "Ед. изм.", "Кол-во"],
    ["Шина автомобильная", "шт", "4"],
    ["Итого", "", "4"],
    ["НДС 20%", "", ""],
  ]);
  const res = extractLotItems(x);
  assert.deepEqual(res.items.map((i) => i.name), ["Шина автомобильная"]);
});

test("список инвентаря товарной таблицей тоже не считается (нет кол-ва/единицы)", () => {
  const x = tbl([
    ["Наименование оборудования и инструментов", "инв №", "зав. №"],
    ["станок", "1", "2"],
  ]);
  assert.equal(extractLotItems(x).status, "no-table", "нет колонки кол-во/ед.изм — не товарная таблица");
  assert.equal(pickGoodsTable(docxTables(x)), null);
});

test("КТРУ-спецификация в приоритете над товарной таблицей", () => {
  // если есть и КТРУ-таблица (с характеристиками), и простая товарная — берём КТРУ
  const spec = tbl([
    ["Наименование товара", "Код КТРУ", "Наименование характеристики", "Значение характеристики"],
    ["Перчатки", "32.50.13.190-00007686", "Размер", "≥ 8"],
  ]);
  const goods = tbl([["Наименование", "Кол-во"], ["Прочее", "3"]]);
  const res = extractLotItems(spec + goods);
  assert.equal(res.status, "ok");
  assert.equal(res.items[0].chars.length, 1);
});

test("потолки не дают одному документу съесть память", () => {
  const rows = [["Наименование товара", "Код КТРУ", "Наименование характеристики", "Значение характеристики"]];
  for (let i = 0; i < 300; i++) rows.push(["Товар " + i, "21.20.23.110-0000000" + (i % 10), "Длина", "не менее 5"]);
  const items = parseSpecTable(pickSpecTable(docxTables(tbl(rows))), { maxItems: 50 });
  assert.equal(items.length, 50);
});

// ───────────────────────────────────────────── таблицы Excel (223-ФЗ) и reqText

test("шапка ниже пустых строк находится (Excel-ТЗ: титул + пустые + шапка)", () => {
  // у 223-ФЗ Excel-ТЗ сверху титул и пустые строки, шапка не в первых трёх
  const tables = [{ rows: [
    ["", "", "Приложение № 2"],
    [], [],
    ["№ п/п", "Наименование товаров", "Нормативно-технические требования", "Единица измерения", "Объем"],
    ["1", "Свинцово-сурьмянистый сплав ССу2", "ГОСТ 1292-81, сурьма не менее 2,8 %", "т", "20"],
  ] }];
  const res = extractLotItemsFromTables(tables);
  assert.equal(res.status, "goods");
  assert.equal(res.items.length, 1);
  assert.equal(res.items[0].name, "Свинцово-сурьмянистый сплав ССу2");
  assert.equal(res.items[0].qty, 20, "«Объем» распознан как количество");
  assert.equal(res.items[0].unit, "т");
});

test("свободная колонка требований → характеристика позиции", () => {
  const tables = [{ rows: [
    ["Наименование товара", "Нормативно-технические требования", "Ед. изм", "Кол-во"],
    ["Свинец ССу2", "ГОСТ 1292-81 с содержанием сурьмы не менее 2,8 %", "т", "20"],
  ] }];
  const it = extractLotItemsFromTables(tables).items[0];
  assert.equal(it.chars.length, 1);
  assert.equal(it.chars[0].key, "Технические требования");
  assert.match(it.chars[0].value, /ГОСТ 1292-81/);
  assert.match(it.chars[0].value, /2,8 %/);
});

test("«объём» опознаётся как количество", () => {
  const map = mapColumns(["Наименование", "Объём поставки", "Ед. изм"]);
  assert.equal(map.qty, 1);
});

test("xlsxSheetsToTables оборачивает листы в таблицы", () => {
  const tables = xlsxSheetsToTables([[["a", "b"], ["c", "d"]], [["x"]]]);
  assert.equal(tables.length, 2);
  assert.deepEqual(tables[0].rows, [["a", "b"], ["c", "d"]]);
});

test("reqText не ломает разбор обычной КТРУ-таблицы (.docx)", () => {
  // у КТРУ-таблицы колонки характеристик — это спец-таблица, reqText не должен
  // перехватывать её в товарную
  const spec = tbl([
    ["Наименование товара", "Код КТРУ", "Наименование характеристики", "Значение характеристики"],
    ["Перчатки", "32.50.13.190-00007686", "Размер", "≥ 8"],
  ]);
  const res = extractLotItems(spec);
  assert.equal(res.status, "ok");
  assert.equal(res.items[0].chars.length, 1);
  assert.equal(res.items[0].chars[0].key, "Размер");
});
