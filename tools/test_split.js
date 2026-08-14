// Тесты разделения снапшота: node --test tools/test_split.js
//
// Разделение опасно ровно одним: потерей. Файл собирается на одной машине,
// разбирается в браузере на другой, и если поле уехало не в тот файл или не
// уехало вовсе, ошибка проявится не падением, а тихо опустевшей карточкой.
// Поэтому главный тест здесь — круговой: разделили и собрали обратно.

const test = require("node:test");
const assert = require("node:assert");

const { splitSnapshot, mergeSidecars, isDegenerateLots, synthLot } = require("./sources/split");

const eis = (over = {}) => ({
  id: "eis_1", number: "1", title: "Поставка перчаток", customer: "ГБУЗ",
  law: "44-ФЗ", price: 1000, region: "Москва", endDate: "2026-09-01T00:00:00",
  lots: [{ name: "Поставка перчаток", qty: "—", unit: "", price: 1000 }],
  documents: [{ id: "d1", name: "ТЗ.docx", url: "https://x/1" }],
  tzTerms: ["перчатк", "нитрилов"], tzDoc: "ТЗ.docx", tzStatus: "ok",
  ...over,
});

const portal = (over = {}) => ({
  id: "portal_9", number: "9", title: "Закупка канцтоваров", customer: "ДЗМ",
  law: "44-ФЗ", price: 5000, endDate: "2026-09-01T00:00:00",
  lots: [
    { name: "Бумага А4", qty: "100", unit: "пачка", price: 3000, okpd: "17.12.14" },
    { name: "Ручки", qty: "500", unit: "шт", price: 2000 },
  ],
  documents: [], tzStatus: "pending",
  ...over,
});

// ───────────────────────────────────────────────────────────── что куда уехало

test("тяжёлые поля покидают ленту", () => {
  const { feed } = splitSnapshot([eis()]);
  for (const k of ["tzTerms", "tzDoc", "documents"]) {
    assert.equal(k in feed[0], false, `${k} обязан уехать в довесок`);
  }
});

test("лёгкие поля остаются в ленте", () => {
  const { feed } = splitSnapshot([eis()]);
  for (const k of ["id", "number", "title", "customer", "price", "region", "endDate", "tzStatus"]) {
    assert.ok(k in feed[0], `${k} нужен для ленты`);
  }
});

test("признак «документы есть» остаётся в ленте", () => {
  const { feed } = splitSnapshot([eis(), eis({ id: "eis_2", documents: [] })]);
  assert.equal(feed[0].docsCount, 1);
  assert.equal(feed[1].docsCount, 0,
    "без этого пустой список неотличим от «довесок ещё не загружен»");
});

test("довески собираются картой по id, а не массивом", () => {
  const { tz, docs } = splitSnapshot([eis(), portal()]);
  assert.deepEqual(Object.keys(tz), ["eis_1"], "у портальной закупки терминов нет");
  assert.deepEqual(Object.keys(docs), ["eis_1"], "у портальной закупки документов нет");
  assert.deepEqual(tz.eis_1.terms, ["перчатк", "нитрилов"]);
  assert.equal(docs.eis_1[0].url, "https://x/1");
});

test("закупка без терминов и документов довесков не порождает", () => {
  const { tz, docs } = splitSnapshot([portal()]);
  assert.deepEqual(tz, {});
  assert.deepEqual(docs, {});
});

// ─────────────────────────────────────────────────────────── вырожденные лоты

test("лот-дубль названия выбрасывается", () => {
  assert.equal(isDegenerateLots(eis()), true);
  const { feed } = splitSnapshot([eis()]);
  assert.equal("lots" in feed[0], false);
});

test("настоящие лоты Портала остаются", () => {
  assert.equal(isDegenerateLots(portal()), false);
  const { feed } = splitSnapshot([portal()]);
  assert.equal(feed[0].lots.length, 2, "потерять позиции лота нельзя — по ним ищут");
});

test("одиночный лот с собственными данными не вырожденный", () => {
  for (const l of [
    { name: "Поставка перчаток", qty: "500", unit: "", price: 1000 },
    { name: "Поставка перчаток", qty: "—", unit: "пара", price: 1000 },
    { name: "Поставка перчаток", qty: "—", unit: "", price: 1000, okpd: "22.19.60" },
    { name: "Поставка перчаток", qty: "—", unit: "", price: 777 },
  ]) {
    assert.equal(isDegenerateLots(eis({ lots: [l] })), false, JSON.stringify(l));
  }
});

test("лот с другим названием не вырожденный", () => {
  assert.equal(isDegenerateLots(eis({ lots: [{ name: "Перчатки нитриловые", price: 1000 }] })), false);
});

// ───────────────────────────────────────────────────────────── круговой тест

test("разделили и собрали обратно — ничего не потеряли", () => {
  const src = [eis(), portal()];
  const before = JSON.parse(JSON.stringify(src));
  const { feed, tz, docs } = splitSnapshot(src);
  const back = mergeSidecars(JSON.parse(JSON.stringify(feed)), tz, docs);

  for (const [i, orig] of before.entries()) {
    const got = back[i];
    for (const k of Object.keys(orig)) {
      assert.deepEqual(got[k], orig[k], `поле ${k} у ${orig.id} не восстановилось`);
    }
  }
});

test("лента без довесков остаётся рабочей", () => {
  // так фронт выглядит ДО того, как подгрузил tz.json/docs.json
  const { feed } = splitSnapshot([eis(), portal()]);
  const back = mergeSidecars(JSON.parse(JSON.stringify(feed)), null, null);
  assert.deepEqual(back[0].documents, [], "не undefined — иначе .length упадёт");
  assert.deepEqual(back[0].lots, [synthLot(eis())], "вырожденный лот восстановлен из названия");
  assert.equal(back[1].lots.length, 2, "настоящие лоты не тронуты");
});

test("спецификация из таблицы КТРУ едет довеском, а не в ленте", () => {
  const items = [{ name: "Перчатки", ktru: "32.50.13.190-00007686", qty: 50, unit: "шт",
                   chars: [{ key: "Размер", operator: "gte", value: 8, unit: "", hardness: "hard", raw: "≥ 8" }] }];
  const { feed, tz } = splitSnapshot([eis({ lotItems: items })]);
  assert.equal("lotItems" in feed[0], false, "позиции с характеристиками тяжелее всего в записи");
  assert.deepEqual(tz.eis_1.items, items);
  const back = mergeSidecars(JSON.parse(JSON.stringify(feed)), tz, {});
  assert.deepEqual(back[0].lotItems, items);
});

test("без спецификации поле items в довеске не появляется", () => {
  const { tz } = splitSnapshot([eis()]);
  assert.equal("items" in tz.eis_1, false, "пустое поле раздуло бы довесок на ровном месте");
});

test("исходный объект не мутируется", () => {
  const p = eis();
  splitSnapshot([p]);
  assert.ok(p.tzTerms && p.documents, "накопитель отдаёт свои записи — портить их нельзя");
});
