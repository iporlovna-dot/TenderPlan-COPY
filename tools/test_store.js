// Тесты накопителя закупок: node tools/test_store.js
//
// Зависимостей нет — встроенный в Node раннер (node:test + node:assert).
// Проверяем ровно то, где ошибка молчит: слияние окна с накопленным (можно
// незаметно затереть добытые документы), выселение (можно накопить мусор
// навсегда или выбросить живую закупку) и пересчёт «дней до дедлайна» (число
// считается в момент сбора, а запись живёт неделями).

const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const S = require("./sources/store");

const DAY = 86400000;
const NOW = Date.UTC(2026, 7, 13, 12, 0, 0);
const inDays = (n) => new Date(NOW + n * DAY).toISOString();

function purchase(id, over = {}) {
  return { id, number: id.replace(/^\w+_/, ""), title: "Закупка " + id,
           endDate: inDays(10), documents: [], ...over };
}

test("свежая запись не затирает добытые документы и термины", () => {
  const old = purchase("eis_1", {
    documents: [{ id: "d1", name: "ТЗ.docx", url: "u" }],
    region: "г. Москва", deliveryDays: 14,
    tzTerms: ["перчатк", "нитрилов"], tzStatus: "ok", tzDoc: "ТЗ.docx", docsFetched: true,
  });
  // окно отдаёт ту же закупку, но без карточки: цена обновилась, документов нет
  const fresh = purchase("eis_1", { price: 999, documents: [], docsFetched: false });

  const m = S.mergeRecord(old, fresh);
  assert.equal(m.price, 999, "свежие поля должны побеждать");
  assert.deepEqual(m.documents, old.documents, "документы обязаны пережить прогон без карточки");
  assert.equal(m.region, "г. Москва");
  assert.equal(m.deliveryDays, 14);
  assert.deepEqual(m.tzTerms, old.tzTerms);
  assert.equal(m.tzStatus, "ok");
  assert.equal(m.docsFetched, true, "«в карточку заходили» — знание накопленное, а не про этот час");
});

test("свежие документы побеждают пустоту в накопителе", () => {
  const old = purchase("eis_2", { documents: [], region: "" });
  const fresh = purchase("eis_2", {
    documents: [{ id: "d9", name: "ТЗ.docx", url: "u" }], region: "Тверская область",
  });
  const m = S.mergeRecord(old, fresh);
  assert.equal(m.documents.length, 1);
  assert.equal(m.region, "Тверская область");
});

test("окно доливает новое, не выбрасывая отсутствующее в нём", () => {
  const store = S.emptyStore();
  S.mergeWindow(store, [purchase("eis_a"), purchase("eis_b")], NOW);
  // следующий прогон: в окне только «b» и новая «c» — «a» из окна ушла, но жива
  const added = S.mergeWindow(store, [purchase("eis_b"), purchase("eis_c")], NOW + 3600000);
  assert.equal(added, 1, "новой должна считаться только «c»");
  assert.deepEqual(Object.keys(store.purchases).sort(), ["eis_a", "eis_b", "eis_c"]);
});

test("выселяются истёкшие, живые остаются", () => {
  const store = S.emptyStore();
  S.mergeWindow(store, [
    purchase("eis_live", { endDate: inDays(5) }),
    purchase("eis_dead", { endDate: inDays(-1) }),
    purchase("eis_edge", { endDate: new Date(NOW).toISOString() }),  // ровно сейчас — уже поздно
  ], NOW);
  const ev = S.evictStore(store, NOW);
  assert.equal(ev.expired, 2);
  assert.deepEqual(Object.keys(store.purchases), ["eis_live"]);
  assert.equal(store.seenAt.eis_dead, undefined, "seenAt не должен пережить свою закупку");
});

test("закупка без срока подачи уходит по возрасту, а не остаётся навсегда", () => {
  const store = S.emptyStore();
  S.mergeWindow(store, [purchase("eis_nodate", { endDate: null })], NOW);
  assert.equal(S.evictStore(store, NOW + 29 * DAY).stale, 0, "29 дней — ещё рано");
  assert.equal(S.evictStore(store, NOW + 31 * DAY).stale, 1, "31 день — пора");
  assert.equal(Object.keys(store.purchases).length, 0);
});

test("дни до дедлайна пересчитываются, а не остаются из момента сбора", () => {
  const p = purchase("eis_x", { endDate: inDays(9), beginDate: inDays(-2), deadlineDays: 9, publishedDaysAgo: 2 });
  S.refreshVolatile(p, NOW + 8 * DAY);       // прошла неделя, запись лежала в накопителе
  assert.equal(p.deadlineDays, 1, "иначе фронт покажет «осталось 9 дней» за сутки до закрытия");
  assert.equal(p.publishedDaysAgo, 10);
});

test("лента отсортирована по близости дедлайна", () => {
  const store = S.emptyStore();
  S.mergeWindow(store, [
    purchase("eis_far", { endDate: inDays(20) }),
    purchase("eis_near", { endDate: inDays(1) }),
    purchase("eis_mid", { endDate: inDays(7) }),
  ], NOW);
  assert.deepEqual(S.sortedPurchases(store).map(p => p.id), ["eis_near", "eis_mid", "eis_far"]);
});

test("накопитель переживает запись и чтение с диска", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "lekalo-store-"));
  const file = path.join(dir, "sub", "store.json");
  const store = S.emptyStore();
  S.mergeWindow(store, [purchase("eis_1", { tzTerms: ["перчатк"], tzStatus: "ok" })], NOW);
  S.saveStore(file, store);

  const back = S.loadStore(file, null);
  assert.deepEqual(back.purchases.eis_1.tzTerms, ["перчатк"]);
  assert.equal(back.seenAt.eis_1, NOW);
  fs.rmSync(dir, { recursive: true, force: true });
});

test("нет файла накопителя — засеваем из прошлого снапшота, а не с нуля", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "lekalo-store-"));
  const snap = path.join(dir, "purchases.json");
  fs.writeFileSync(snap, JSON.stringify({
    purchases: [purchase("eis_1", { tzTerms: ["перчатк"], tzStatus: "ok" }), purchase("pp_2")],
  }), "utf8");

  const store = S.loadStore(path.join(dir, "нет-такого.json"), snap, () => {});
  assert.equal(Object.keys(store.purchases).length, 2, "иначе первый прогон выбросит уже добытые ТЗ");
  assert.deepEqual(store.purchases.eis_1.tzTerms, ["перчатк"]);
  fs.rmSync(dir, { recursive: true, force: true });
});

test("битый или чужой по версии файл не роняет сборку", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "lekalo-store-"));
  const bad = path.join(dir, "store.json");

  fs.writeFileSync(bad, "{это не json", "utf8");
  assert.deepEqual(S.loadStore(bad, null).purchases, {});

  fs.writeFileSync(bad, JSON.stringify({ __v: S.STORE_V + 1, purchases: { eis_1: purchase("eis_1") } }), "utf8");
  assert.deepEqual(S.loadStore(bad, null).purchases, {}, "чужую версию читать нельзя — схема могла измениться");
  fs.rmSync(dir, { recursive: true, force: true });
});
