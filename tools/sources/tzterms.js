// Термины ТЗ закупки для «Умной сверки».
//
// Зачем отдельный шаг. Сверить своё ТЗ с закупкой можно только по тексту ТЗ, а в
// снапшоте его нет: у закупки ЕИС там одно название (~12 слов), ОКПД пуст у всех,
// лоты дублируют название. Сам текст лежит в приложенном документе, и достать его
// может ТОЛЬКО этот сборщик:
//   • браузер не может — ссылки ведут прямо на zakupki.gov.ru/zakupki.mos.ru,
//     скачивание работает навигацией, а прочитать содержимое можно лишь fetch'ем,
//     который зарежет CORS;
//   • VPS не может — он заблокирован источниками (CLAUDE.md, «Грабли» §1).
//
// Поэтому сюда: скачиваем ТЗ, вынимаем термины и кладём в закупку компактным
// массивом. Не текст — текст раздул бы снапшот в разы, а для сравнения он не нужен:
// сравнение и так идёт по множествам терминов.
//
// Термины считает ОБЩИЙ с фронтом модуль (site/js/tzmatch.js): если бы алгоритм
// стемминга разъехался между сборщиком и браузером, один и тот же документ дал бы
// разные проценты. Он же умеет .docx без единой зависимости — ZIP + DecompressionStream,
// они есть и в браузере, и в Node 18+.
//
// Разбираем только .docx (и файлы без расширения, которые на деле ZIP — их узнаём
// по сигнатуре PK). Это не лень: по факту в снапшоте .docx — 432 из ~615 документов,
// похожих на ТЗ, то есть 70%. Остальное (pdf 16, doc 29, xls/xlsx 24, архивы 19)
// требует тяжёлых парсеров ради хвоста; честнее отдать статус «формат не разобран»,
// чем показать процент, посчитанный неизвестно по чему.

const { curlBinary, mapLimit } = require("./util");
const LKTZ = require("../../site/js/tzmatch.js");

const CONC = Number(process.env.LK_TZ_CONC || 4);
// сколько терминов оставляем на закупку: длинные ТЗ дают тысячи, но снапшот и так
// ~8 МБ. 120 самых длинных покрывают числовые требования и специфичные слова, а
// отсекают общую воду — она всё равно есть у всех и на различение не влияет.
const MAX_TERMS = Number(process.env.LK_TZ_MAX_TERMS || 120);

// Имена, по которым документ похож на ТЗ. Порядок важен: «техническое задание»
// точнее, чем «описание объекта закупки», а то и другое точнее, чем случайный файл.
const TZ_NAME_HINTS = [
  "техническое задание", "тех. задание", "техзадание", "тз",
  "описание объекта", "объект закупки", "характеристик", "спецификац",
];

function pickTzDoc(documents) {
  const docs = (documents || []).filter(d => d && d.url);
  if (!docs.length) return null;
  const scored = docs.map(d => {
    const name = (d.name || "").toLowerCase();
    let score = 0;
    TZ_NAME_HINTS.forEach((hint, i) => {
      if (name.includes(hint)) score = Math.max(score, TZ_NAME_HINTS.length - i);
    });
    // .docx предпочитаем при равном имени — только его мы и умеем разобрать
    if (name.endsWith(".docx")) score += 0.5;
    return { doc: d, score };
  });
  scored.sort((a, b) => b.score - a.score);
  // ничего не похоже на ТЗ по имени — берём первый .docx, если он есть: у ЕИС
  // имена часто без расширения и без слова «задание», но документ всё равно тот
  return scored[0].score > 0 ? scored[0].doc
    : (docs.find(d => (d.name || "").toLowerCase().endsWith(".docx")) || docs[0]);
}

// ZIP (а значит потенциально .docx) начинается с "PK\x03\x04". Проверяем байты, а
// не имя: у ЕИС масса документов вообще без расширения в названии.
function looksLikeZip(buf) {
  return buf && buf.length > 4 && buf[0] === 0x50 && buf[1] === 0x4b
    && (buf[2] === 0x03 || buf[2] === 0x05 || buf[2] === 0x07);
}

// В русском слов длиннее этого практически нет — всё, что длиннее, на живых ТЗ
// оказывалось склейкой разбора («овощерезательнопротирочн»). Такой мусор не
// совпадёт ни с чьим ТЗ, но, будучи самым длинным, вытеснял настоящие термины.
const MAX_TERM_LEN = 24;

// Из множества терминов оставить самые информативные.
// Порядок: сначала измеримые требования (число + единица) — это то, по чему
// заявку реально отклоняют; потом слова по длине как мере специфичности
// («нитриловые» несёт больше, чем «шт»).
function topTerms(set, limit) {
  const clean = [...set].filter(t =>
    t.length <= MAX_TERM_LEN
    // длинные числовые «слова» — это КТРУ, ИНН, номера позиций: они уникальны у
    // каждой закупки, совпасть не могут в принципе и только занимают место
    && !/^\d{5,}$/.test(t));
  const measured = clean.filter(t => /\d/.test(t) && /[а-яё%]/i.test(t));
  const measuredSet = new Set(measured);
  const words = clean.filter(t => !measuredSet.has(t));
  const byLen = (a, b) => b.length - a.length || a.localeCompare(b);
  measured.sort(byLen); words.sort(byLen);

  // Бюджет делим пополам, а не отдаём числам целиком. Проверено на реальном ТЗ
  // (57 тыс. знаков, 1577 терминов): при приоритете чисел все 120 слотов
  // занимали размеры, и слов не оставалось вовсе. Это ломает сверку — точная
  // строка «1161 мм» совпадёт у двух документов почти никогда, а «нитрилов» и
  // «перчатк» задают категорию и совпадают. Нужны оба сигнала: слова говорят
  // «это про мой товар», числа — «и по параметрам проходим».
  // Недобор одной стороны отдаём другой, чтобы бюджет не пропадал.
  const half = Math.ceil(limit / 2);
  const takeMeasured = Math.min(measured.length, Math.max(half, limit - words.length));
  return measured.slice(0, takeMeasured).concat(words.slice(0, limit - takeMeasured));
}

// Разобрать один документ → термины. Возвращает { terms, docName, status }.
// status: ok | unsupported (не docx) | empty (разобрали, но текста нет — скан) | error
async function termsForDoc(doc) {
  let buf;
  try {
    buf = await curlBinary(doc.url);
  } catch (e) {
    return { terms: [], docName: doc.name || "", status: "error" };
  }
  if (!looksLikeZip(buf)) return { terms: [], docName: doc.name || "", status: "unsupported" };
  let text;
  try {
    text = await LKTZ.docxTextFromBytes(new Uint8Array(buf));
  } catch (e) {
    // это ZIP, но не Word (часто .xlsx под именем без расширения) — не наш формат
    return { terms: [], docName: doc.name || "", status: "unsupported" };
  }
  const set = LKTZ.terms(text || "");
  if (!set.size) return { terms: [], docName: doc.name || "", status: "empty" };
  return { terms: topTerms(set, MAX_TERMS), docName: doc.name || "", status: "ok" };
}

// Проставить закупкам tzTerms/tzDoc/tzStatus. Мутирует переданные закупки.
// `cache` — накопительный, тот же приём, что у документов ЕИС: ТЗ у закупки не
// меняется от часа к часу, а перекачивать его каждый прогон — тысячи мегабайт.
// `budget` — сколько документов скачиваем за прогон.
async function annotateTz(purchases, cache = {}, budget = 120) {
  const need = [];
  let reused = 0, noDoc = 0;
  for (const p of purchases) {
    const hit = cache[p.id];
    if (hit) { applyTz(p, hit); reused++; continue; }
    const doc = pickTzDoc(p.documents);
    if (!doc) {
      // Пустой documents[] значит разное. У Портала карточка приходит целиком, и
      // пусто — это правда «приложений нет». У ЕИС в карточку надо заходить
      // отдельно, и до большинства закупок бюджет docsLimit ещё не дошёл — там
      // честный ответ «ещё не смотрели», а не «документов нет».
      p.tzStatus = p.docsFetched === false ? "pending" : "no-doc";
      if (p.tzStatus === "no-doc") noDoc++;
      continue;
    }
    need.push({ p, doc });
  }
  const queue = need.slice(0, budget);
  // остальным честно говорим, что до них просто ещё не дошли — это не «нет ТЗ»
  need.slice(budget).forEach(({ p }) => { p.tzStatus = "pending"; });

  await mapLimit(queue, CONC, async ({ p, doc }) => {
    const res = await termsForDoc(doc);
    cache[p.id] = res;
    applyTz(p, res);
  }, (k, t) => process.stdout.write(`\r  ТЗ: разбираю ${k}/${t}`));
  if (queue.length) process.stdout.write("\n");

  const ok = purchases.filter(p => p.tzStatus === "ok").length;
  console.log(`  ТЗ: ${ok} закупок с терминами | скачано сейчас ${queue.length}, `
    + `из кэша ${reused}, без документов ${noDoc}, ждут очереди ${Math.max(0, need.length - budget)}`);
  return cache;
}

function applyTz(p, res) {
  p.tzStatus = res.status;
  p.tzDoc = res.docName || "";
  if (res.terms && res.terms.length) p.tzTerms = res.terms;
}

module.exports = { annotateTz, pickTzDoc, looksLikeZip, topTerms, termsForDoc };
