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

const CONC = Number(process.env.LK_TZ_CONC || 8);
// сколько терминов оставляем на закупку: длинные ТЗ дают тысячи, но снапшот и так
// ~9 МБ. 120 отобранных (см. topTerms) покрывают предмет и числовые требования,
// а вода отсекается — она есть у всех и на различение не влияет.
const MAX_TERMS = Number(process.env.LK_TZ_MAX_TERMS || 120);

// Имена, по которым документ похож на ТЗ. Порядок важен: «техническое задание»
// точнее, чем «описание объекта закупки», а то и другое точнее, чем случайный файл.
const TZ_NAME_HINTS = [
  "техническое задание", "тех. задание", "техзадание", "тз",
  "описание объекта", "объект закупки", "характеристик", "спецификац",
];

// Форматы, которые мы заведомо не разберём. Скачивать их незачем: ответ известен
// заранее, а трафик и место в бюджете реальные. `.zip`/`.rar` тоже сюда — архив
// проходит проверку сигнатуры PK, но Word'ом не оказывается, и скачивание уходит
// впустую. Файлы БЕЗ расширения не в списке намеренно: у ЕИС их половина всех
// документов (5702 из 11427), и среди них полно настоящих .docx.
const HOPELESS_EXT = /\.(pdf|docx?|xlsx?|rtf|odt|ods|txt|jpe?g|png|tiff?|sig|zip|rar|7z|gz)$/i;
function isHopeless(name) {
  const n = (name || "").trim().toLowerCase();
  // .docx разбираем — из общего запрета на doc-подобные его надо вернуть
  return HOPELESS_EXT.test(n) && !n.endsWith(".docx");
}

// Кандидаты в ТЗ, от самого правдоподобного к наименее. Раньше здесь выбирался
// РОВНО ОДИН документ, и если он оказывался не .docx, закупка получала
// `unsupported` навсегда — при том что рядом в той же закупке лежали другие
// файлы. Замер на 40 таких закупках: перебор следующих кандидатов спасает 68%
// ценой ~1.7 лишних скачиваний.
function rankTzDocs(documents) {
  const docs = (documents || []).filter(d => d && d.url && !isHopeless(d.name));
  return docs
    .map(d => {
      const name = (d.name || "").toLowerCase();
      let score = 0;
      TZ_NAME_HINTS.forEach((hint, i) => {
        if (name.includes(hint)) score = Math.max(score, TZ_NAME_HINTS.length - i);
      });
      // .docx предпочитаем при равном имени — только его мы и умеем разобрать
      if (name.endsWith(".docx")) score += 0.5;
      return { doc: d, score };
    })
    .sort((a, b) => b.score - a.score)
    .map(x => x.doc);
}

function pickTzDoc(documents) { return rankTzDocs(documents)[0] || null; }

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

// Из терминов документа оставить самые информативные.
// Принимает КАРТУ частот (основа → сколько раз встретилась), а не множество:
// частота — единственный дешёвый признак предмета закупки. Отбор слов по длине,
// который стоял здесь раньше, выбирал ровно наоборот: в русском длинные слова
// канцелярские («конкурентоспособност», «антитеррористическ»), а предмет
// короткий. Замер на живом снапшоте: у ТЗ на перчатки слова «перчатк» в
// сохранённых терминах не оказывалось вовсе, зато 24% мест занимал шаблон,
// встречающийся у 70–83% всех ТЗ.
function topTerms(freq, limit) {
  const entries = [...(freq instanceof Map ? freq : new Map(Object.entries(freq || {})))]
    .filter(([t]) =>
      t.length <= MAX_TERM_LEN
      // длинные числовые «слова» — это КТРУ, ИНН, номера позиций: они уникальны у
      // каждой закупки, совпасть не могут в принципе и только занимают место
      && !/^\d{5,}$/.test(t));
  const measured = entries.filter(([t]) => /\d/.test(t) && /[а-яё%]/i.test(t)).map(([t]) => t);
  const measuredSet = new Set(measured);
  const words = entries.filter(([t]) => !measuredSet.has(t));
  // Измеримые требования частотой не ранжируются: «0,1 мм» стоит в документе
  // один раз, а «2 шт» может повторяться в каждой строке спецификации — здесь
  // информативнее длина, она отделяет «1195-1205 мм» от «5 шт».
  measured.sort((a, b) => b.length - a.length || a.localeCompare(b));
  const wordList = words
    .sort((a, b) => b[1] - a[1] || b[0].length - a[0].length || a[0].localeCompare(b[0]))
    .map(([t]) => t);

  // Бюджет делим пополам, а не отдаём числам целиком. Проверено на реальном ТЗ
  // (57 тыс. знаков, 1577 терминов): при приоритете чисел все 120 слотов
  // занимали размеры, и слов не оставалось вовсе. Это ломает сверку — точная
  // строка «1161 мм» совпадёт у двух документов почти никогда, а «нитрилов» и
  // «перчатк» задают категорию и совпадают. Нужны оба сигнала: слова говорят
  // «это про мой товар», числа — «и по параметрам проходим».
  // Недобор одной стороны отдаём другой, чтобы бюджет не пропадал.
  const half = Math.ceil(limit / 2);
  const takeMeasured = Math.min(measured.length, Math.max(half, limit - wordList.length));
  return measured.slice(0, takeMeasured).concat(wordList.slice(0, limit - takeMeasured));
}

// Сколько кандидатов перебирать. Хвост длинных списков — это, как правило,
// проекты контракта и обоснования НМЦК, а не ТЗ: перебирать все 20 приложений
// ради них дорого, а отдача падает.
const MAX_TRIES = Number(process.env.LK_TZ_TRIES || 4);

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
  const freq = LKTZ.termFreq(text || "");
  if (!freq.size) return { terms: [], docName: doc.name || "", status: "empty" };
  return { terms: topTerms(freq, MAX_TERMS), docName: doc.name || "", status: "ok" };
}

// Перебрать кандидатов закупки, пока один не разберётся. `spend()` списывает
// одно скачивание с общего бюджета прогона и отвечает, было ли что списывать.
//
// Если бюджет кончился на середине перебора, возвращаем `pending`, а НЕ вердикт
// последней неудачи: «не разобралось» и «мы не дочитали до конца» — разные вещи,
// и закэшированный на полпути `unsupported` закрыл бы закупке дорогу навсегда.
// `parse` — шов для тестов: перебор кандидатов проверяется без сети.
async function termsForPurchase(docs, spend, parse = termsForDoc) {
  const tries = Math.min(MAX_TRIES, docs.length);
  let lastBad = null;
  for (let i = 0; i < tries; i++) {
    if (!spend()) return { terms: [], docName: "", status: "pending", spent: i };
    const res = await parse(docs[i]);
    if (res.status === "ok") return { ...res, spent: i + 1 };
    lastBad = res;
  }
  return { ...lastBad, spent: tries };
}

// Термины у закупки уже есть — значит её ТЗ когда-то разобрали. Такая запись
// приходит из накопителя (tools/sources/store.js) и переживает прогоны.
function hasTerms(p) { return Boolean(p.tzTerms && p.tzTerms.length); }

// Компаратор очереди разбора: ни разу не разобранные — вперёд.
function unparsedFirst(a, b) { return Number(hasTerms(a.p)) - Number(hasTerms(b.p)); }

// Проставить закупкам tzTerms/tzDoc/tzStatus. Мутирует переданные закупки.
// `cache` — накопительный, тот же приём, что у документов ЕИС: ТЗ у закупки не
// меняется от часа к часу, а перекачивать его каждый прогон — тысячи мегабайт.
// `budget` — сколько документов скачиваем за прогон.
async function annotateTz(purchases, cache = {}, budget = 120) {
  const need = [];
  let reused = 0, noDoc = 0, hopeless = 0;
  for (const p of purchases) {
    const hit = cache[p.id];
    if (hit) { applyTz(p, hit); reused++; continue; }
    // Если термины у закупки уже есть, статус не трогаем вовсе: он про них и
    // рассказывает, а «документов не видели» означало бы, что мы забыли
    // собственную работу.
    const known = hasTerms(p);
    const docs = rankTzDocs(p.documents);
    if (!docs.length) {
      if (known) continue;
      if ((p.documents || []).length) {
        // Приложения есть, но все — форматы, которые мы не разбираем. Это
        // окончательный ответ, и он не стоит ни одного скачивания: расширение
        // уже всё сказало. Кэшируем, чтобы не пересчитывать каждый прогон.
        const res = { terms: [], docName: (p.documents[0].name || ""), status: "unsupported" };
        cache[p.id] = res; applyTz(p, res); hopeless++;
        continue;
      }
      // Пустой documents[] значит разное. У Портала карточка приходит целиком, и
      // пусто — это правда «приложений нет». У ЕИС в карточку надо заходить
      // отдельно, и до большинства закупок бюджет docsLimit ещё не дошёл — там
      // честный ответ «ещё не смотрели», а не «документов нет».
      p.tzStatus = p.docsFetched === false ? "pending" : "no-doc";
      if (p.tzStatus === "no-doc") noDoc++;
      continue;
    }
    need.push({ p, docs });
  }
  // Сначала те, у кого терминов нет вовсе: разбор такой закупки добавляет знание,
  // а повторный разбор уже разобранной лишь обновляет версию алгоритма. Без этого
  // бюджет уходил бы на ближайшие по дедлайну независимо от того, знаем мы про них
  // что-нибудь или нет, и накопитель разрастался бы немыми записями.
  // Сортировка стабильна (Node 11+), поэтому внутри каждой группы сохраняется
  // порядок по близости дедлайна, заданный вызывающим кодом.
  need.sort(unparsedFirst);

  // Бюджет считает СКАЧИВАНИЯ, а не закупки: с перебором кандидатов закупка
  // стоит от одного до MAX_TRIES документов, и ограничивать надо то, что
  // упирается в канал и во время, — трафик. Отсюда общий счётчик на прогон,
  // а очередь берём с запасом: сколько успеется, столько и разберём.
  let left = budget;
  const spend = () => (left > 0 ? (left--, true) : false);
  const queue = need.slice(0, budget);
  // остальным честно говорим, что до них просто ещё не дошли — это не «нет ТЗ»
  need.slice(budget).forEach(({ p }) => { if (!hasTerms(p)) p.tzStatus = "pending"; });

  let done = 0, retried = 0;
  await mapLimit(queue, CONC, async ({ p, docs }) => {
    const res = await termsForPurchase(docs, spend);
    if (res.spent > 1) retried++;
    // `pending` означает «бюджет кончился на середине перебора» — такой вердикт
    // кэшировать нельзя, иначе закупка застрянет на нём навсегда.
    if (res.status !== "pending") cache[p.id] = res;
    applyTz(p, res);
    done++;
  }, () => process.stdout.write(`\r  ТЗ: разбираю ${done}/${queue.length}, скачиваний ${budget - left}`));
  if (queue.length) process.stdout.write("\n");
  const spent = budget - left;

  // Термины у закупки есть только если разбор когда-то удался (applyTz кладёт их
  // единственным путём — из успешного termsForDoc). Значит статус обязан это
  // отражать, чем бы запись ни была помечена раньше: прогон до появления этой
  // проверки успел записать «ждёт очереди» полутора тысячам уже разобранных
  // закупок. Чиним здесь, в одном месте на выходе, а не в каждой ветке выше.
  for (const p of purchases) if (hasTerms(p) && p.tzStatus !== "ok") p.tzStatus = "ok";

  // Считаем именно термины, а не статус «ok»: запись из накопителя несёт термины
  // прошлых прогонов, и по статусу их не видно — так строка врала бы в разы.
  const withTerms = purchases.filter(hasTerms).length;
  const waiting = Math.max(0, need.length - queue.length);
  console.log(`  ТЗ: ${withTerms} закупок с терминами | разобрано сейчас ${done} `
    + `за ${spent} скачиваний (с перебором кандидатов ${retried}), `
    + `из кэша ${reused}, формат не тот без скачивания ${hopeless}, без документов ${noDoc}, `
    + `ждут очереди ${waiting} (из них ни разу не разобраны `
    + `${need.slice(queue.length).filter(({ p }) => !hasTerms(p)).length})`);
  return cache;
}

function applyTz(p, res) {
  p.tzStatus = res.status;
  p.tzDoc = res.docName || "";
  if (res.terms && res.terms.length) p.tzTerms = res.terms;
}

module.exports = { annotateTz, pickTzDoc, rankTzDocs, isHopeless, looksLikeZip, topTerms,
                    termsForDoc, termsForPurchase, hasTerms, unparsedFirst };
