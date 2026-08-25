// Накопитель активных закупок — состояние сборщика между прогонами.
//
// Зачем он вообще есть. Снапшот собирался заново каждый прогон из окна ЕИС
// «последние обновлённые», а окно ВРАЩАЕТСЯ. Замер по двум прод-снапшотам с
// разницей в три дня: из 3484 закупок ЕИС, которые на вторую дату были ЕЩЁ
// АКТИВНЫ, из снапшота выпали 3022 — 87%. Они не истекли, они просто ушли из
// окна. А кэши чистились по составу снапшота, то есть вместе с закупкой
// выбрасывались и скачанные для неё документы, и разобранные термины ТЗ.
// Работа каждого часа наполовину уходила в мусор, и покрытие «Умной сверки»
// стояло на 38% при 24 × 220 разборов в сутки на пул из 4149 закупок.
//
// Здесь закупка живёт до истечения срока подачи, а окно только доливает новое.
// Файл машинно-специфичен и не деплоится (.gitignore), как и остальные кэши.

const fs = require("fs");
const path = require("path");

const DAY = 86400000;
const STORE_V = 1;

// Закупка без срока подачи по сроку не выселится — чтобы такие не копились
// вечно, у них свой предел по возрасту с момента, когда её видели в окне.
const TTL_MS = Number(process.env.LK_STORE_TTL_DAYS || 30) * DAY;
// Страховка от неограниченного роста файла, а не рабочий лимит: нормальный
// размер задаёт истечение сроков.
const MAX = Number(process.env.LK_STORE_MAX || 80000);

// Поля, которые добываются отдельными дорогими заходами (карточка закупки,
// скачивание документа ТЗ). У свежей записи из окна их может не быть — сборщик
// в этот час просто до неё не дошёл. Затирать ими уже добытое нельзя, иначе
// накопитель забывает ровно то, ради чего заведён.
const CARRY = ["documents", "region", "deliveryDays", "deliveryPlace", "okpd",
               "tzTerms", "tzDoc", "tzStatus", "tzAlgo", "tzItems", "lotItems", "contacts",
               "customerInn"];

function endTs(p) { return p && p.endDate ? new Date(p.endDate).getTime() : Infinity; }

function isEmptyValue(v) {
  return v == null || v === "" || (Array.isArray(v) && v.length === 0);
}

// Свежая запись побеждает во всём, кроме добытого дорого и отсутствующего у неё.
function mergeRecord(old, fresh) {
  if (!old) return fresh;
  const out = { ...fresh };
  for (const k of CARRY) if (isEmptyValue(out[k]) && !isEmptyValue(old[k])) out[k] = old[k];
  // «в карточку заходили» — свойство накопленного знания, а не текущего прогона
  if (old.docsFetched) out.docsFetched = true;
  return out;
}

function emptyStore() { return { purchases: {}, seenAt: {} }; }

// Первый запуск: накопителя ещё нет, но прошлый снапшот на диске лежит и в нём
// уже есть документы и термины. Начинать с нуля значило бы их выбросить — тот
// же приём, что в tools/seed_docs_cache.js.
function bootstrapStore(snapshotFile, log = console.log) {
  const store = emptyStore();
  try {
    const list = JSON.parse(fs.readFileSync(snapshotFile, "utf8")).purchases || [];
    const now = Date.now();
    for (const p of list) if (p && p.id) { store.purchases[p.id] = p; store.seenAt[p.id] = now; }
    if (list.length) log(`  накопитель: пуст — засеял из прошлого снапшота, ${list.length} закупок`);
  } catch (e) { /* снапшота нет — стартуем с чистого, это не ошибка */ }
  return store;
}

function loadStore(file, snapshotFile, log = console.log) {
  try {
    const raw = JSON.parse(fs.readFileSync(file, "utf8"));
    if (raw && raw.__v === STORE_V && raw.purchases) {
      return { purchases: raw.purchases, seenAt: raw.seenAt || {} };
    }
  } catch (e) { /* нет файла / битый / чужая версия */ }
  return snapshotFile ? bootstrapStore(snapshotFile, log) : emptyStore();
}

function saveStore(file, store) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify({
    __v: STORE_V, savedAt: new Date().toISOString(),
    purchases: store.purchases, seenAt: store.seenAt,
  }), "utf8");
}

// Влить окно в накопитель. Возвращает, сколько закупок оказались новыми.
function mergeWindow(store, fresh, now) {
  let added = 0;
  for (const p of fresh) {
    if (!p || !p.id) continue;
    if (!store.purchases[p.id]) added++;
    store.purchases[p.id] = mergeRecord(store.purchases[p.id], p);
    store.seenAt[p.id] = now;
  }
  return added;
}

// Выселение: истёкшие — по сроку подачи, бессрочные — по возрасту, и общий
// потолок на всякий случай (лишнее режем с дальнего конца по дедлайну).
function evictStore(store, now) {
  const drop = (id) => { delete store.purchases[id]; delete store.seenAt[id]; };
  let expired = 0, stale = 0, overflow = 0;
  for (const [id, p] of Object.entries(store.purchases)) {
    const end = p.endDate ? new Date(p.endDate).getTime() : null;
    if (end != null) { if (end <= now) { drop(id); expired++; } }
    else if (now - (store.seenAt[id] || 0) > TTL_MS) { drop(id); stale++; }
  }
  const ids = Object.keys(store.purchases);
  if (ids.length > MAX) {
    ids.sort((a, b) => endTs(store.purchases[a]) - endTs(store.purchases[b]));
    for (const id of ids.slice(MAX)) { drop(id); overflow++; }
  }
  return { expired, stale, overflow };
}

// deadlineDays/publishedDaysAgo считаются в момент сбора, а запись живёт в
// накопителе неделями — пересчитываем перед записью в снапшот, иначе фронт
// показал бы «осталось 9 дней» у закупки, которая закрывается завтра.
function refreshVolatile(p, now) {
  const end = p.endDate ? new Date(p.endDate).getTime() : null;
  p.deadlineDays = end ? Math.max(0, Math.ceil((end - now) / DAY)) : 0;
  const begin = p.beginDate ? new Date(p.beginDate).getTime() : null;
  p.publishedDaysAgo = begin ? Math.max(0, Math.round((now - begin) / DAY)) : 0;
}

// Закупки накопителя в порядке ленты: ближайший дедлайн сверху.
function sortedPurchases(store) {
  return Object.values(store.purchases).sort((a, b) => endTs(a) - endTs(b));
}

module.exports = {
  endTs, mergeRecord, mergeWindow, evictStore, refreshVolatile, sortedPurchases,
  loadStore, saveStore, bootstrapStore, emptyStore, CARRY, STORE_V, TTL_MS, MAX,
};
