/* Сверка «своё ТЗ ↔ ТЗ закупки»: извлечение текста, термины, покрытие.
   Порт server/app/matching.py.

   Работает В ДВУХ СРЕДАХ — в браузере и в Node 18+ (сборщик снапшота):
   разбор .docx опирается только на DecompressionStream/TextDecoder/DataView,
   которые есть и там, и там. Общий файл, а не третья копия алгоритма, потому что
   термины закупки готовит сборщик (`tools/sources/tzterms.js`), а сравнивает с
   ними ТЗ пользователя фронт — разъехавшийся стемминг дал бы разные проценты
   на одних и тех же данных. См. CLAUDE.md, «Умная сверка». */

const LKTZ = (() => {

  const UNIT = "(?:мм|см|м|кг|г|мл|л|шт|мкм|%|мпа|вт|квт|в|гц|гост|iso|ту)";
  // Конец единицы измерения — через отрицательный просмотр вперёд, а НЕ через \b.
  // В JS \b опирается на \w = [A-Za-z0-9_], кириллица туда не входит: после «мм»
  // граница слова не возникает никогда, и регэксп не матчил ни «0,1 мм», ни
  // «500 шт» — то есть числовые требования не извлекались вообще. В Python-порте
  // (server/app/matching.py) тот же шаблон работает, потому что там \b юникодный.
  // Та же грабля уже стоила цикла сборки на сроках ЕИС (см. скилл lekalo-ship).
  // Захватываем ТОЛЬКО само требование: знак сравнения, необязательное начало
  // диапазона, число, единица. Раньше шаблон начинался с «[a-zа-яё0-9().,-]*» и
  // тащил в термин всё, что стояло перед числом, — на живых ТЗ получались
  // «черепаха-2, 18 мм» и «документами. 6. в». Такие строки у каждого документа
  // свои, поэтому числовые требования не совпадали НИКОГДА.
  const NUM_RE = new RegExp(
    "(?:[\\u2265\\u2264=<>]\\s*)?" +
    "(?:\\d+(?:[.,]\\d+)?\\s*[-\\u2013\\u2014]\\s*)?" +
    "\\d+(?:[.,]\\d+)?\\s*" + UNIT + "(?![а-яёa-z])", "giu");

  // Даты и номера реестров, притворяющиеся требованиями. Единицы «г» и «в»
  // односимвольные и в русском совпадают с суффиксом года («2011 г.») и
  // предлогом («в»), а КТРУ/ОГРН — просто длинные числа. Отличить их регэкспом
  // единиц нельзя, отличаем по виду самого числа.
  // Проверять надо ОБЕ ветки разбора: «2011г» приходит не из числового шаблона,
  // а из словесного — `[а-яёa-z0-9]{4,}` глотает цифры с буквой целиком, и
  // такой «термин» потом считается измеримым требованием.
  function isNumericJunk(t) {
    return /(?:19|20)\d{2}\s*г$/.test(t)   // «2011 г» — год, а не граммы
      || /^(?:19|20)\d{2}$/.test(t)        // голый год: он есть в каждом ТЗ, различать нечего
      || /\d{6,}/.test(t);                 // КТРУ/ОГРН/номер позиции, а не размер
  }
  const WORD_RE = /[а-яёa-z0-9]{4,}/giu;
  const STOP = ["поставка","закупка","товар","оказание","услуги","выполнение","работ","нужд",
    // «должен» и «должн» — обе формы: стеммер даёт «должно»→«должн», и с «долже»
    // (первые 5 букв «должен») этот префикс не совпадает. Одной записи мало.
    "государственн","бюджетн","учреждение","который","должен","должн","дней","также","соответствии",
    "требования","наличие","договор","контракт",
    // Лексика самой процедуры закупки, а не товара. Добавлено по замеру живого
    // прогона: при отборе слов по частоте в топ выходили «срок», «заказчик»,
    // «качеств», «должн» — они есть в каждом ТЗ и не различают НИЧЕГО, но
    // съедали места в бюджете из 120 терминов.
    // Сюда только процедурные слова. Предметные («материал», «размер», «объект»)
    // НЕ трогаем: они шумные, но для части закупок это и есть товар, а вес по
    // редкости их и так придавит на сравнении.
    // ⚠️ Сверка идёт по ПЕРВЫМ ПЯТИ буквам (см. STOP.some ниже), поэтому слово
    // сюда попадает, только если его пятибуквенный префикс не задевает товары.
    // Не вошли по этой причине: «техническ» (срезал бы «техника»), «установленн»
    // («установка»), «производитс» («производительность» — настоящее требование),
    // «накладн» («накладка»), «участник» («участок»), «приемк» («приемник»).
    "заказчик","подрядчик","поставщик","исполнител","срок","оплат","гарант",
    "ответственн","обязательств","документац","условия","порядок","качеств",
    "обеспечен","изменен","стоимост","заявк","предусмотрен","осуществля","являетс",
    // «Российская Федерация», «наименование», «должно быть» — верх частотного
    // топа живого прогона, к товару отношения не имеют
    "российск","федераци","наименован","быть"];

  // не ровно «-2 буквы» — иначе «клинки»→«клин» засчитает «клинику» как совпадение.
  const RU_ENDINGS = [
    "иями", "иях",
    "ами", "ями", "его", "ому", "ыми", "ими", "ого",
    "ах", "ях", "ов", "ев", "ий", "ый", "их", "ых", "ая", "яя", "ое", "ые", "ие", "ом", "ем", "им", "ым", "ой", "ей",
    "а", "я", "о", "е", "и", "ы", "у", "ю", "й", "ь",
  ];
  const STEM_MIN = 4;
  function stem(w) {
    w = w.toLowerCase();
    if (w.length <= STEM_MIN) return w;
    for (const suf of RU_ENDINGS) {
      if (w.length - suf.length >= STEM_MIN && w.endsWith(suf)) return w.slice(0, w.length - suf.length);
    }
    return w;
  }
  // «беглая гласная»: клинОк → клинка/клинки теряют «о» перед «к» в косвенных формах
  // и множественном числе — суффиксный стеммер этого не видит (буква пропадает
  // ВНУТРИ слова). Добавляем вариант без гласной в набор терминов ДОПОЛНИТЕЛЬНО
  // (не взамен) — так «клинок» в закупке и «клинки» в своём ТЗ пересекутся по
  // термину «клинк», не ломая остальные совпадения.
  function fleetingVowelVariant(word) {
    const m = /^(.+[бвгджзйклмнпрстфхцчшщ])[оеё]к$/i.exec(word || "");
    return m ? m[1] + "к" : null;
  }

  // Частоты, а не множество. Сколько раз основа встретилась в документе — это и
  // есть сигнал «о чём ТЗ»: в ТЗ на перчатки «перчатк» стоит полсотни раз, а
  // «законодательств» — три. Раньше мы это выбрасывали (хранили Set) и отбирали
  // термины по ДЛИНЕ — а в русском длинные слова канцелярские
  // («конкурентоспособност»), предмет же короткий («перчатк»). Замер на живом
  // снапшоте: предмета закупки в сохранённых терминах не оказывалось вовсе.
  function termFreq(text) {
    const freq = new Map();
    const bump = (t) => freq.set(t, (freq.get(t) || 0) + 1);
    let m;
    NUM_RE.lastIndex = 0;
    while ((m = NUM_RE.exec(text))) {
      const t = m[0].trim().toLowerCase().replace(/\s+/g, " ");
      if (!isNumericJunk(t)) bump(t);
    }
    WORD_RE.lastIndex = 0;
    while ((m = WORD_RE.exec(text))) {
      const s = stem(m[0]);
      if (isNumericJunk(s)) continue;
      // Вариант с беглой гласной здесь НЕ добавляем. В снапшоте у закупки всего
      // 120 мест, а вариант удваивал каждое слово на «-ок»: «срок» приводил с
      // собой «срк». Расширение делается на стороне ТЗ пользователя
      // (expandVariants) — оно покрывает обе стороны сразу, потому что пересечение
      // множеств симметрично, и не стоит ни одного места в снапшоте.
      if (!STOP.some(x => s.startsWith(x.slice(0, 5)))) bump(s);
    }
    return freq;
  }

  function terms(text) {
    return new Set(termFreq(text).keys());
  }

  // Добавить варианты с беглой гласной («клинок» → ещё и «клинк»). Применяется к
  // ТЗ ПОЛЬЗОВАТЕЛЯ: его набор живёт в localStorage целиком и в бюджет снапшота
  // не упирается. Одностороннего расширения достаточно — сравнение это
  // пересечение множеств, и «клинок» у меня встретится с «клинк» из закупки
  // ровно так же, как «клинк» у меня с «клинок» из закупки.
  function expandVariants(termSet) {
    const out = new Set(termSet);
    termSet.forEach(t => {
      const alt = fleetingVowelVariant(t);
      if (alt) out.add(alt);
    });
    return out;
  }

  // ---------- редкость термина по корпусу ----------

  // Вес термина = насколько он редок. Термин, который есть у всех ТЗ, весит ~0.
  // Без этого «характеристик» (83% всех ТЗ) весил столько же, сколько «нитрилов»,
  // и два НИКАК не связанных ТЗ совпадали на 16% — шумовой пол, на котором любой
  // показанный процент врал. С весом по редкости пол падает до 6%.
  function makeIdf(df, docCount) {
    const N = Math.max(1, docCount || 0);
    const get = df instanceof Map ? (t) => df.get(t) || 0 : (t) => (df && df[t]) || 0;
    return (t) => Math.log((N + 1) / (get(t) + 1));
  }

  // ---------- предмет ТЗ ----------

  // «Предмет» — что за товар, а не какие у него параметры. Берём основы, частые у
  // меня и редкие в корпусе. Числа сюда не пускаем: «0,1 мм» — это требование к
  // предмету, а не сам предмет, и в названии закупки его не бывает.
  const SUBJECT_MIN_LEN = 5;
  function subjectTerms(freq, idf, limit = 10) {
    const scored = [];
    freq.forEach((tf, t) => {
      if (t.length < SUBJECT_MIN_LEN || /\d/.test(t)) return;
      scored.push({ term: t, weight: tf * (idf ? idf(t) : 1) });
    });
    scored.sort((a, b) => b.weight - a.weight || a.term.localeCompare(b.term));
    return scored.slice(0, limit);
  }

  // Читаемая форма слова для показа человеку: основа «перчатк» в интерфейсе
  // выглядит обрубком, а «перчатки» — нормально. Берём самую частую исходную
  // форму из текста. Считается ТОЛЬКО для ТЗ пользователя: в снапшоте лежат одни
  // основы, восстановить по ним исходное написание уже нельзя.
  function surfaceForms(text) {
    const byStem = new Map();          // основа → Map(форма → сколько раз)
    let m;
    WORD_RE.lastIndex = 0;
    while ((m = WORD_RE.exec(text))) {
      const w = m[0].toLowerCase();
      const s = stem(w);
      if (!byStem.has(s)) byStem.set(s, new Map());
      const forms = byStem.get(s);
      forms.set(w, (forms.get(w) || 0) + 1);
    }
    const out = {};
    byStem.forEach((forms, s) => {
      let best = s, top = -1;
      forms.forEach((c, w) => { if (c > top) { top = c; best = w; } });
      out[s] = best;
    });
    return out;
  }

  // Основы текста закупки (название + лоты) — по ним ищем предмет. Отдельно от
  // terms(): здесь не нужны ни частоты, ни отсев стоп-слов, нужен быстрый Set.
  function stemSet(text) {
    const set = new Set();
    let m;
    WORD_RE.lastIndex = 0;
    while ((m = WORD_RE.exec(text || ""))) {
      const s = stem(m[0]);
      set.add(s);
      const alt = fleetingVowelVariant(s);
      if (alt) set.add(alt);
    }
    return set;
  }

  // Совпал ли предмет: доля веса предметных слов, найденных в тексте закупки.
  // Это главный сигнал ленты — название и лоты есть у 100% закупок, тогда как
  // разобранное ТЗ пока у 4,6%.
  function subjectMatch(subject, hayStems) {
    const total = (subject || []).reduce((s, x) => s + x.weight, 0);
    if (!total) return { score: 0, matched: [] };
    const matched = subject.filter(x => hayStems.has(x.term));
    if (!matched.length) return { score: 0, matched: [] };
    const got = matched.reduce((s, x) => s + x.weight, 0);
    return { score: Math.round(100 * got / total), matched: matched.map(x => x.term) };
  }

  function compare(purchaseText, productText) {
    return compareTerms(terms(purchaseText), terms(productText));
  }

  // Ядро сравнения — на уже извлечённых терминах, а не на тексте. Отдельно от
  // compare() потому, что в ленте термины закупки приходят готовыми из снапшота
  // (их извлёк сборщик из .docx, который браузеру недоступен из-за CORS), и
  // перегонять их обратно в текст ради одного API было бы враньём и потерей.
  //
  // Совпадение — строгое, по множествам. Раньше тут был подстрочный перебор
  // (`haveArr.some(h => h.includes(t) || t.includes(h))`), и он ушёл по двум
  // причинам. Во-первых, цена: в ленте сравнение гоняется по всем ~4200 закупкам
  // на каждую сортировку, а перебор — это O(терминов ТЗ × моих терминов) на
  // закупку, счёт шёл на сотни миллионов операций и подвешивал вкладку.
  // Во-вторых, честность: обе стороны теперь проходят ОДИН стеммер (термины
  // закупки готовит сборщик этим же файлом), поэтому настоящее совпадение и так
  // даёт точное равенство, а подстрока добавляла ложные — ровно тот случай, о
  // котором предупреждает комментарий у RU_ENDINGS: «клиника» покрывала «клин».
  // Измеримое требование — число с единицей («0,1 мм», «500 шт»). Именно по ним
  // отклоняют заявку, поэтому в карточке они идут отдельным списком, а не тонут
  // среди слов.
  function isMeasured(t) { return /\d/.test(t) && /[а-яё%]/i.test(t); }

  function compareTerms(reqTerms, haveTerms, idf) {
    const req = reqTerms instanceof Set ? reqTerms : new Set(reqTerms || []);
    const have = haveTerms instanceof Set ? haveTerms : new Set(haveTerms || []);
    if (!req.size) {
      return { score: 0, verdict: "eligible_with_gaps", covered: [], missing: [],
        measured: { covered: [], missing: [] },
        explanation: "Не удалось извлечь требования из текста ТЗ закупки (пустой текст или скан)." };
    }
    const covered = [], missing = [];
    [...req].sort().forEach(t => (have.has(t) ? covered : missing).push(t));

    // Вес по редкости, а не «одно требование — один голос». Без него термин из
    // 83% всех ТЗ («характеристик») значил столько же, сколько «нитрилов».
    const w = idf ? idf : () => 1;
    const sum = (arr) => arr.reduce((s, t) => s + w(t), 0);
    const total = sum(covered) + sum(missing);
    const score = total > 0 ? Math.round(100 * sum(covered) / total) : 0;

    const measured = {
      covered: covered.filter(isMeasured),
      missing: missing.filter(isMeasured),
    };
    let verdict, expl;
    if (score >= 60) { verdict = "eligible"; expl = "Требования закупки в основном покрыты вашим ТЗ."; }
    else if (score >= 30) { verdict = "eligible_with_gaps"; expl = "Совпадение частичное — посмотрите, чего не хватает."; }
    else { verdict = "disqualified"; expl = "Совпадение слабое — вероятно, закупка про другой товар."; }
    return { score, verdict, covered, missing, measured,
      explanation: `${expl} Совпало ${covered.length} требований из ${req.size}.` };
  }

  // ---------- извлечение текста из файла ----------

  async function extractText(file) {
    const name = (file.name || "").toLowerCase();
    if (name.endsWith(".txt") || file.type === "text/plain") return await file.text();
    if (name.endsWith(".docx")) return await readDocx(file);
    if (name.endsWith(".pdf")) throw new Error("PDF пока не поддерживается — вставьте текст или загрузите .docx/.txt");
    // попробуем как текст
    return await file.text();
  }

  // Разбор через центральный каталог ZIP (конец файла), а не побайтовый поиск
  // локальных заголовков: в локальном заголовке размер может быть обнулён при
  // потоковой записи (data descriptor после данных) — тогда старый побайтовый
  // разбор ломался или брал «до конца файла» как размер одного вложения.
  // Центральный каталог всегда содержит настоящий размер и офсет — так же, как
  // это делают JSZip/zip.js/python zipfile.
  async function readDocx(file) {
    return await docxTextFromBytes(new Uint8Array(await file.arrayBuffer()));
  }

  // Тот же разбор, но от голых байт — так им пользуется сборщик в Node, где
  // никакого File нет, а есть буфер скачанного документа.
  async function docxTextFromBytes(buf) {
    if (!(buf instanceof Uint8Array)) buf = new Uint8Array(buf);
    const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);

    const EOCD_SIG = 0x06054b50;
    let eocdOff = -1;
    const minPos = Math.max(0, buf.length - 22 - 65535); // комментарий в конце — до 65535 байт
    for (let p = buf.length - 22; p >= minPos; p--) {
      if (dv.getUint32(p, true) === EOCD_SIG) { eocdOff = p; break; }
    }
    if (eocdOff < 0) throw new Error("Не похоже на .docx (нет конца центрального каталога ZIP) — вставьте текст вручную");

    const cdSize = dv.getUint32(eocdOff + 12, true);
    const cdOffset = dv.getUint32(eocdOff + 16, true);

    const CD_SIG = 0x02014b50;
    let p = cdOffset;
    const cdEnd = cdOffset + cdSize;
    let target = null;
    while (p < cdEnd && p + 46 <= buf.length) {
      if (dv.getUint32(p, true) !== CD_SIG) break;
      const method = dv.getUint16(p + 10, true);
      const compSize = dv.getUint32(p + 20, true);
      const nameLen = dv.getUint16(p + 28, true);
      const extraLen = dv.getUint16(p + 30, true);
      const commentLen = dv.getUint16(p + 32, true);
      const localOffset = dv.getUint32(p + 42, true);
      const name = new TextDecoder().decode(buf.slice(p + 46, p + 46 + nameLen));
      if (name === "word/document.xml") { target = { method, compSize, localOffset }; break; }
      p += 46 + nameLen + extraLen + commentLen;
    }
    if (!target) throw new Error("В файле нет word/document.xml — это не .docx? Вставьте текст вручную");

    // локальный заголовок нужен только за именем/extra-полем (их длины могут
    // отличаться от копии в центральном каталоге), сам размер — уже из target
    const lp = target.localOffset;
    const lNameLen = dv.getUint16(lp + 26, true);
    const lExtraLen = dv.getUint16(lp + 28, true);
    const dataStart = lp + 30 + lNameLen + lExtraLen;
    const data = buf.slice(dataStart, dataStart + target.compSize);

    let xmlBytes;
    if (target.method === 0) xmlBytes = data;
    else {
      const ds = new DecompressionStream("deflate-raw");
      const stream = new Blob([data]).stream().pipeThrough(ds);
      xmlBytes = new Uint8Array(await new Response(stream).arrayBuffer());
    }
    let xml = new TextDecoder("utf-8").decode(xmlBytes);
    // Разделители расставляем ДО срезания тегов. Иначе соседние куски слипаются:
    // на живых ТЗ получались «директорруководител», «переключателемtimberk»,
    // «зубр,станина,глубина 3,5 мм» — и такие склейки, будучи самыми длинными,
    // выигрывали отбор топ-N и вытесняли настоящие термины.
    // Внутри абзаца runs (<w:r>/<w:t>) склеиваем БЕЗ пробела намеренно: Word рвёт
    // на runs даже середину слова (правки, язык, проверка орфографии), и пробел
    // там разорвал бы слово пополам.
    xml = xml
      .replace(/<w:(?:br|cr)\b[^>]*>/g, "\n")     // разрыв строки внутри абзаца
      .replace(/<w:tab\b[^>]*>/g, " ")            // табуляция — разделитель колонок
      .replace(/<\/w:(?:p|tc|tr)>/g, "\n")        // абзац, ячейка, строка таблицы
      .replace(/<[^>]+>/g, "");
    // &amp; — последним, иначе двойная распаковка (&amp;lt; → &lt; → <)
    return xml.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&");
  }

  return { compare, compareTerms, extractText, terms, termFreq, stem, docxTextFromBytes,
    makeIdf, subjectTerms, subjectMatch, stemSet, isMeasured, expandVariants, surfaceForms };
})();

// Сборщик (Node) подключает этот же файл через require, чтобы термины закупки и
// термины пользовательского ТЗ считались ОДНИМ кодом. В браузере ветка не
// исполняется: `module` там не определён.
if (typeof module !== "undefined" && module.exports) module.exports = LKTZ;
