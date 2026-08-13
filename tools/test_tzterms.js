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

const { annotateTz, hasTerms, unparsedFirst } = require("./sources/tzterms");

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
