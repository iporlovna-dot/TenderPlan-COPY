// Тесты дешёвых путей адаптера ЕИС: node --test tools/test_eis.js
//
// Оба приёма здесь экономят запросы, и оба ошибаются молча: неверно собранный
// адрес вкладки документов даст пустой список (неотличимо от «приложений нет»),
// а неверный справочник регионов проставит закупке чужую область — и она
// пропадёт из фильтра, не подав виду. Сеть не трогаем.

const test = require("node:test");
const assert = require("node:assert");

const { docsUrlDirect } = require("./sources/eis");
const { buildRegionIndex, regionFromNumber, regionCode } = require("./sources/eisregion");

// ---------------------------------------------------------------- documents.html

test("44-ФЗ: вкладка документов строится из адреса карточки", () => {
  const link = "https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=0820500000826004123";
  assert.equal(docsUrlDirect(link),
    "https://zakupki.gov.ru/epz/order/notice/ea20/view/documents.html?regNumber=0820500000826004123");
});

test("44-ФЗ: способ закупки в адресе бывает разный", () => {
  for (const proc of ["ea20", "zk20", "ok20", "ezt20"]) {
    const link = `https://zakupki.gov.ru/epz/order/notice/${proc}/view/common-info.html?regNumber=0123456789012345678`;
    assert.ok(docsUrlDirect(link) && docsUrlDirect(link).includes(`/${proc}/view/documents.html`), proc);
  }
});

test("223-ФЗ коротким путём не ходит — там нужен guid из карточки", () => {
  const link = "https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber=32616260335";
  assert.equal(docsUrlDirect(link), null, "иначе получим 301 на страницу, отдающую 404, и пустой список документов");
});

test("мусорные и пустые ссылки не превращаются в адрес", () => {
  for (const link of ["", null, undefined, "https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html",
                      "https://example.com/common-info.html?regNumber=1"]) {
    assert.equal(docsUrlDirect(link), null, String(link));
  }
});

// ---------------------------------------------------------------- регион по номеру

// Номер извещения 44-ФЗ — ровно 19 цифр, код региона в 3-4-й. Собираем так, а не
// строкой наугад: короткий номер молча не пройдёт проверку и тест позеленеет зря.
function num44(code, seq) {
  const n = "03" + code + String(seq).padStart(15, "0");
  assert.equal(n.length, 19, "тестовый номер сам должен быть валидным");
  return n;
}
const p44 = (number, region, over = {}) => ({ number, region, law: "44-ФЗ", ...over });
const many = (n, fn) => Array.from({ length: n }, (_, i) => fn(i));

test("код региона — цифры 3-4 номера 44-ФЗ", () => {
  assert.equal(regionCode("0372100003426000762"), "72");
  assert.equal(regionCode("32616260335"), "", "номер 223-ФЗ короче — там этих цифр не существует");
  assert.equal(regionCode(""), "");
});

test("справочник строится по большинству и применяется", () => {
  const learned = buildRegionIndex([
    ...many(9, i => p44(num44("72", i), "г. Санкт-Петербург")),
    p44(num44("72", 99), "Тверская область"),   // одиночная ошибка в данных
  ]);
  assert.equal(learned.get("72"), "г. Санкт-Петербург");
  assert.equal(regionFromNumber(num44("72", 555), learned), "г. Санкт-Петербург");
});

test("кода мало примеров — в справочник не берём", () => {
  const learned = buildRegionIndex(many(2, i => p44(num44("81", i), "Тверская область")));
  assert.equal(learned.size, 0, "два примера ничего не доказывают — пусть закупка сходит в карточку");
});

test("код мешает регионы — в справочник не берём", () => {
  const mixed = [
    ...many(5, i => p44(num44("55", i), "Тверская область")),
    ...many(5, i => p44(num44("55", 100 + i), "Омская область")),
  ];
  assert.equal(buildRegionIndex(mixed).size, 0, "50/50 — доверять нечему");
});

test("догадка не учит сама себя", () => {
  const guessed = many(9, i => p44(num44("72", i), "г. Санкт-Петербург", { regionGuessed: true }));
  assert.equal(buildRegionIndex(guessed).size, 0,
    "иначе одна ошибка размножится и подтвердит сама себя");
});

test("незнакомый код честно возвращает пустоту", () => {
  const learned = buildRegionIndex(many(9, i => p44(num44("72", i), "г. Санкт-Петербург")));
  assert.equal(regionFromNumber(num44("91", 1), learned), "",
    "пусто — значит вызывающий пойдёт в карточку и пополнит справочник");
  assert.equal(regionFromNumber("32616260335", learned), "", "223-ФЗ приёмом не покрывается");
});

test("223-ФЗ не попадает в обучение", () => {
  const learned = buildRegionIndex(many(9, i => ({ number: `3261626033${i}`, region: "г. Москва", law: "223-ФЗ" })));
  assert.equal(learned.size, 0);
});
