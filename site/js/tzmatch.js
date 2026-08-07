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
  const NUM_RE = new RegExp("[a-zа-яё0-9().,-]*\\s*[\\u2265\\u2264=<>]?\\s*\\d+[.,]?\\d*\\s*" + UNIT + "(?![а-яёa-z])", "giu");
  const WORD_RE = /[а-яёa-z0-9]{4,}/giu;
  const STOP = ["поставка","закупка","товар","оказание","услуги","выполнение","работ","нужд",
    "государственн","бюджетн","учреждение","который","должен","также","соответствии",
    "требования","наличие","договор","контракт"];

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

  function terms(text) {
    const set = new Set();
    let m;
    NUM_RE.lastIndex = 0;
    while ((m = NUM_RE.exec(text))) set.add(m[0].trim().toLowerCase().replace(/\s+/g, " "));
    WORD_RE.lastIndex = 0;
    while ((m = WORD_RE.exec(text))) {
      const s = stem(m[0]);
      if (!STOP.some(x => s.startsWith(x.slice(0, 5)))) {
        set.add(s);
        const alt = fleetingVowelVariant(s);
        if (alt) set.add(alt);
      }
    }
    return set;
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
  function compareTerms(reqTerms, haveTerms) {
    const req = reqTerms instanceof Set ? reqTerms : new Set(reqTerms || []);
    const have = haveTerms instanceof Set ? haveTerms : new Set(haveTerms || []);
    if (!req.size) {
      return { score: 0, verdict: "eligible_with_gaps", checks: [],
        explanation: "Не удалось извлечь требования из текста ТЗ закупки (пустой текст или скан)." };
    }
    const covered = [], missing = [];
    [...req].sort().forEach(t => (have.has(t) ? covered : missing).push(t));
    const score = Math.round(100 * covered.length / req.size);
    const checks = [];
    covered.slice(0, 8).forEach(t => checks.push({ req: t, status: "pass" }));
    missing.slice(0, 8).forEach(t => checks.push({ req: t, status: "gap", note: "нет в вашем ТЗ" }));
    let verdict, expl;
    if (score >= 80) { verdict = "eligible"; expl = "Ваш товар/ТЗ покрывает большинство требований."; }
    else if (score >= 45) { verdict = "eligible_with_gaps"; expl = "Частичное совпадение — проверьте пробелы."; }
    else { verdict = "disqualified"; expl = "Совпадение низкое — вероятно, речь о другом товаре."; }
    return { score, verdict, checks,
      explanation: `${expl} Покрыто ${covered.length} из ${req.size} требований.` };
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
    xml = xml.replace(/<\/w:p>/g, "\n").replace(/<[^>]+>/g, "");
    // &amp; — последним, иначе двойная распаковка (&amp;lt; → &lt; → <)
    return xml.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&");
  }

  return { compare, compareTerms, extractText, terms, stem, docxTextFromBytes };
})();

// Сборщик (Node) подключает этот же файл через require, чтобы термины закупки и
// термины пользовательского ТЗ считались ОДНИМ кодом. В браузере ветка не
// исполняется: `module` там не определён.
if (typeof module !== "undefined" && module.exports) module.exports = LKTZ;
