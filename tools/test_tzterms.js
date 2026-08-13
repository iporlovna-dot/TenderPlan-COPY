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
  annotateTz, hasTerms, unparsedFirst, rankTzDocs, isHopeless, termsForPurchase,
} = require("./sources/tzterms");

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

// ------------------------------------------- перебор кандидатов вместо одного

const doc = (name) => ({ id: name, name, url: "https://example.invalid/" + name });

test("заведомо неразбираемые форматы опознаются по имени", () => {
  for (const n of ["ТЗ.pdf", "смета.xls", "проект.doc", "прил.rar", "скан.JPEG", "подпись.sig", "арх.zip"]) {
    assert.equal(isHopeless(n), true, n);
  }
  for (const n of ["ТЗ.docx", "Приложение 1", "техзадание", "файл.DOCX"]) {
    assert.equal(isHopeless(n), false, n);
  }
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
