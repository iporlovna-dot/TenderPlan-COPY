// Дата-срезы выдачи ЕИС: какой кусок корпуса читать в этом прогоне.
//
// Зачем. Общий список ЕИС отсортирован по дате обновления, и каждый прогон читает
// одну и ту же «голову». Глубже 5000 записей выдача не пускает вообще: страница
// 100 и любая дальше отдают клон последней настоящей (см. CLAUDE.md, грабли §12).
// Значит, сколько ни увеличивай LK_EIS_TAKE, за пределы этих 5000 самых недавно
// обновлённых не выйти — а активных закупок в ЕИС кратно больше.
//
// Обход — нарезка запроса по ДАТЕ ПУБЛИКАЦИИ: publishDateFrom=publishDateTo=день
// даёт непересекающиеся выборки (замер: 10.08 против 11.08 — пересечение 0), и у
// каждой свой потолок в 5000. Плюс разделение по закону: один день 223-ФЗ влезает
// целиком (1000–2000), один день 44-ФЗ сам упирается в потолок.
//
// Этот модуль — только БУХГАЛТЕРИЯ срезов: какие бывают, какой брать следующим,
// докуда дочитали. Сеть — в eis.js. Состояние переживает прогоны: без него
// «следующий срез» каждый раз оказывался бы первым, и мы читали бы ту же голову,
// только другой формы.

const fs = require("fs");
const path = require("path");

const SWEEP_V = 1;
const DAY = 86400000;

// Окно дней публикации. Закупка с ещё не истёкшим сроком подачи почти всегда
// опубликована недавно, поэтому дальше назад срезы пустеют, а состояние по ним
// копится зря.
const DAYS = Number(process.env.LK_EIS_SWEEP_DAYS || 21);
const LAWS = [44, 223];

// Сколько срезов трогать за прогон и на сколько страниц углубляться в каждый.
// Держать так, чтобы приток НЕ обгонял LK_EIS_DOCS: закупка без документов — это
// запись со статусом «ждёт очереди», и если её добывать быстрее, чем разгребать,
// очередь растёт вечно, а покрытие сверки не двигается.
const SLICES = Number(process.env.LK_EIS_SWEEP_SLICES || 4);
const SLICE_PAGES = Number(process.env.LK_EIS_SLICE_PAGES || 12);
const SWEEP_TAKE = Number(process.env.LK_EIS_SWEEP_TAKE || 800);

function dayLabel(ts) {
  const d = new Date(ts);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()}`;
}

function sliceKey(law, day) { return law + "|" + day; }

// Срезы начинаются со ВЧЕРА, а не с сегодня: сегодняшний день ещё пополняется,
// и пометить его «вычерпан» значило бы соврать — а сегодняшние публикации и так
// приходят общим списком «последние обновлённые».
function enumerateSlices(now, days = DAYS, laws = LAWS) {
  const out = [];
  for (let back = 1; back <= days; back++) {
    const day = dayLabel(now - back * DAY);
    for (const law of laws) out.push({ law, day, back, key: sliceKey(law, day) });
  }
  return out;
}

function emptySweep() { return { v: SWEEP_V, slices: {} }; }

// Ещё не вычерпанные, сначала ни разу не тронутые, дальше — по давности захода.
// Среди равных вперёд идут свежие дни: их закупки проживут в накопителе дольше,
// значит добытые для них документы окупятся большим числом прогонов.
function pickSlices(state, slices, count = SLICES) {
  const at = (s) => {
    const st = state.slices[s.key];
    return st ? (st.sweptAt || 0) : -1;
  };
  return slices
    .filter(s => !(state.slices[s.key] || {}).done)
    .sort((a, b) => (at(a) - at(b)) || (a.back - b.back))
    .slice(0, count)
    .map(s => ({ ...s, page: (state.slices[s.key] || {}).page || 1 }));
}

function advance(state, slice, res, now = Date.now()) {
  const prev = state.slices[slice.key] || {};
  state.slices[slice.key] = {
    page: res.nextPage,
    done: Boolean(res.done),
    sweptAt: now,
    got: (prev.got || 0) + (res.got || 0),
  };
}

// Дни, выпавшие из окна, забываем — иначе файл растёт без предела.
function pruneSweep(state, slices) {
  const live = new Set(slices.map(s => s.key));
  let dropped = 0;
  for (const k of Object.keys(state.slices)) {
    if (!live.has(k)) { delete state.slices[k]; dropped++; }
  }
  return dropped;
}

function sweepStats(state, slices) {
  let done = 0, touched = 0, got = 0;
  for (const s of slices) {
    const st = state.slices[s.key];
    if (!st) continue;
    touched++;
    if (st.done) done++;
    got += st.got || 0;
  }
  return { total: slices.length, touched, done, got };
}

function loadSweep(file) {
  try {
    const raw = JSON.parse(fs.readFileSync(file, "utf8"));
    if (!raw || raw.v !== SWEEP_V || !raw.slices) return emptySweep();
    return raw;
  } catch (e) { return emptySweep(); }   // нет файла/битый — начинаем с чистого
}

function saveSweep(file, state) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(state), "utf8");
}

module.exports = {
  SWEEP_V, DAYS, SLICES, SLICE_PAGES, SWEEP_TAKE,
  dayLabel, sliceKey, enumerateSlices, emptySweep, pickSlices, advance,
  pruneSweep, sweepStats, loadSweep, saveSweep,
};
