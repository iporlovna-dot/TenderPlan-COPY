// Тесты дата-срезов ЕИС: node --test tools/test_eissweep.js
//
// Всё здесь ломается молча. Съехавший курсор перечитает те же страницы (прогон
// «работает», а корпус не растёт); преждевременное «вычерпан» навсегда выкинет
// кусок закупок; чтение за 100-й страницей принесёт клон последней настоящей и
// будет выглядеть как честные данные. Сеть не трогаем — у sweepSlice есть шов.

const test = require("node:test");
const assert = require("node:assert");

const {
  enumerateSlices, pickSlices, advance, pruneSweep, emptySweep, sweepStats,
  dayLabel, sliceKey,
} = require("./sources/eissweep");
const { sweepSlice, CLAMP_PAGE } = require("./sources/eis");

const NOW = Date.UTC(2026, 7, 14, 12, 0, 0);   // 14.08.2026

// ------------------------------------------------------------- перечень срезов

test("срезы начинаются со вчера — сегодняшний день ещё пополняется", () => {
  const days = new Set(enumerateSlices(NOW, 3).map(s => s.day));
  assert.ok(!days.has(dayLabel(NOW)), "пометить сегодняшний срез «вычерпан» значило бы соврать");
  assert.deepEqual([...days], [dayLabel(NOW - 86400000), dayLabel(NOW - 2 * 86400000),
                               dayLabel(NOW - 3 * 86400000)]);
});

test("на каждый день — свой срез по каждому закону", () => {
  const slices = enumerateSlices(NOW, 2);
  assert.equal(slices.length, 4, "44-ФЗ за день сам упирается в потолок — вместе с 223 не влезет");
  assert.deepEqual(slices.map(s => s.law), [44, 223, 44, 223]);
});

test("день выводится в том виде, который понимает источник", () => {
  assert.equal(dayLabel(Date.UTC(2026, 0, 5, 15)), "05.01.2026");
});

// -------------------------------------------------------------- выбор среза

test("ни разу не тронутые идут первыми, свежие дни впереди", () => {
  const slices = enumerateSlices(NOW, 3);
  const state = emptySweep();
  state.slices[sliceKey(44, dayLabel(NOW - 86400000))] = { page: 5, sweptAt: NOW, done: false };
  const picked = pickSlices(state, slices, 2).map(s => s.key);
  assert.deepEqual(picked, [sliceKey(223, dayLabel(NOW - 86400000)),
                            sliceKey(44, dayLabel(NOW - 2 * 86400000))]);
});

test("среди тронутых первым берётся самый давний", () => {
  const slices = enumerateSlices(NOW, 2);
  const state = emptySweep();
  for (const [i, s] of slices.entries()) {
    state.slices[s.key] = { page: 2, sweptAt: NOW - i * 1000, done: false };
  }
  assert.equal(pickSlices(state, slices, 1)[0].key, slices[3].key, "у него sweptAt меньше всех");
});

test("вычерпанный срез больше не выбирается", () => {
  const slices = enumerateSlices(NOW, 1);
  const state = emptySweep();
  state.slices[slices[0].key] = { page: 30, sweptAt: NOW, done: true };
  assert.deepEqual(pickSlices(state, slices, 5).map(s => s.key), [slices[1].key]);
});

test("выбранный срез несёт курсор, а не начинается заново", () => {
  const slices = enumerateSlices(NOW, 1);
  const state = emptySweep();
  state.slices[slices[0].key] = { page: 37, sweptAt: NOW, done: false };
  const picked = pickSlices(state, slices, 5).find(s => s.key === slices[0].key);
  assert.equal(picked.page, 37,
    "иначе каждый прогон перечитывал бы первые страницы — корпус не растёт");
});

// -------------------------------------------------------------- курсор и уборка

test("advance двигает курсор и копит добытое", () => {
  const state = emptySweep();
  const slice = { key: "44|13.08.2026" };
  advance(state, slice, { nextPage: 13, got: 500, done: false }, NOW);
  advance(state, slice, { nextPage: 25, got: 400, done: true }, NOW + 1);
  assert.deepEqual(state.slices[slice.key], { page: 25, done: true, sweptAt: NOW + 1, got: 900 });
});

test("дни, выпавшие из окна, забываются", () => {
  const state = emptySweep();
  state.slices["44|01.01.2020"] = { page: 3, done: false, sweptAt: 0 };
  const slices = enumerateSlices(NOW, 2);
  state.slices[slices[0].key] = { page: 2, done: false, sweptAt: NOW };
  assert.equal(pruneSweep(state, slices), 1);
  assert.deepEqual(Object.keys(state.slices), [slices[0].key]);
});

test("статистика считает по живому окну, а не по файлу", () => {
  const slices = enumerateSlices(NOW, 2);
  const state = emptySweep();
  state.slices[slices[0].key] = { page: 99, done: true, sweptAt: NOW, got: 4900 };
  state.slices[slices[1].key] = { page: 5, done: false, sweptAt: NOW, got: 200 };
  assert.deepEqual(sweepStats(state, slices), { total: 4, touched: 2, done: 1, got: 5100 });
});

// -------------------------------------------------------------- обход страниц

// Поддельная выдача: `pages` записей на страницу, дальше пусто. Номер закупки
// делаем из номера страницы и позиции — так видно, что именно прочитали.
function fakeSource(realPages, perPage = 50) {
  const seenPages = [];
  const fetch = async (page) => {
    seenPages.push(page);
    if (page > realPages) return "";
    return Array.from({ length: perPage }, (_, i) =>
      `search-registry-entry-block regNumber=${page}${String(i).padStart(3, "0")}`).join("");
  };
  return { fetch, seenPages };
}

test("обход продолжается с курсора, а не с первой страницы", async () => {
  const src = fakeSource(100);
  const res = await sweepSlice(new Set(), [], { law: 44, day: "13.08.2026", page: 7 },
                               1e9, 3, src.fetch);
  assert.deepEqual(src.seenPages, [7, 8, 9]);
  assert.equal(res.nextPage, 10, "следующий прогон обязан начать отсюда");
  assert.equal(res.done, false);
});

test("пустая страница — срез вычерпан", async () => {
  const src = fakeSource(2);
  const items = [];
  const res = await sweepSlice(new Set(), items, { law: 223, day: "13.08.2026", page: 1 },
                               1e9, 10, src.fetch);
  assert.equal(res.done, true);
  assert.equal(items.length, 100);
  assert.equal(res.pages, 3, "три запроса: две страницы с данными и одна пустая");
});

test("за 100-ю страницу не ходим — там клон последней настоящей", async () => {
  const src = fakeSource(1000);
  const res = await sweepSlice(new Set(), [], { law: 44, day: "13.08.2026", page: CLAMP_PAGE - 2 },
                               1e9, 50, src.fetch);
  assert.deepEqual(src.seenPages, [98, 99], "страница 100 отдала бы копию 99-й как настоящую");
  assert.equal(res.done, true, "глубже выдача не пускает — срез закрыт, сколько бы там ни осталось");
});

test("бюджет записей останавливает обход, но срез не объявляется вычерпанным", async () => {
  const src = fakeSource(100);
  const res = await sweepSlice(new Set(), [], { law: 44, day: "13.08.2026", page: 1 },
                               120, 50, src.fetch);
  assert.equal(res.got, 150, "страницу дочитываем целиком — выбрасывать уже скачанное незачем");
  assert.equal(res.pages, 3);
  assert.equal(res.done, false, "мы просто остановились, а не дошли до конца");
});

test("уже виденные закупки не считаются добычей", async () => {
  const src = fakeSource(100);
  const seen = new Set(Array.from({ length: 50 }, (_, i) => `1${String(i).padStart(3, "0")}`));
  const items = [];
  await sweepSlice(seen, items, { law: 44, day: "13.08.2026", page: 1 }, 1e9, 2, src.fetch);
  assert.equal(items.length, 50, "первая страница целиком уже была в общем списке");
});

test("сетевая ошибка не съедает курсор", async () => {
  let calls = 0;
  const fetch = async (page) => {
    calls++;
    if (page === 5) throw new Error("connection reset");
    return "search-registry-entry-block regNumber=" + page + "001";
  };
  const res = await sweepSlice(new Set(), [], { law: 44, day: "13.08.2026", page: 4 },
                               1e9, 10, fetch);
  assert.equal(res.nextPage, 5, "вернёмся ровно к несработавшей странице");
  assert.equal(res.done, false, "иначе одно моргание канала выкинуло бы кусок корпуса навсегда");
  assert.equal(calls, 2);
});
