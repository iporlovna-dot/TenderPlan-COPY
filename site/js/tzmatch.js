/* Клиентская сверка «своё ТЗ ↔ ТЗ закупки».
   Извлекает текст из .txt/.docx прямо в браузере (docx — ZIP + DecompressionStream),
   вытаскивает значимые термины и считает покрытие. Порт server/app/matching.py. */

const LKTZ = (() => {

  const UNIT = "(?:мм|см|м|кг|г|мл|л|шт|мкм|%|мпа|вт|квт|в|гц|гост|iso|ту)";
  const NUM_RE = new RegExp("[a-zа-яё0-9().,-]*\\s*[\\u2265\\u2264=<>]?\\s*\\d+[.,]?\\d*\\s*" + UNIT + "\\b", "giu");
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

  function terms(text) {
    const set = new Set();
    let m;
    NUM_RE.lastIndex = 0;
    while ((m = NUM_RE.exec(text))) set.add(m[0].trim().toLowerCase().replace(/\s+/g, " "));
    WORD_RE.lastIndex = 0;
    while ((m = WORD_RE.exec(text))) {
      const s = stem(m[0]);
      if (!STOP.some(x => s.startsWith(x.slice(0, 5)))) set.add(s);
    }
    return set;
  }

  function compare(purchaseText, productText) {
    const req = terms(purchaseText);
    const have = terms(productText);
    const haveArr = [...have];
    const haveJoin = haveArr.join(" ");
    if (!req.size) {
      return { score: 0, verdict: "eligible_with_gaps", checks: [],
        explanation: "Не удалось извлечь требования из текста ТЗ закупки (пустой текст или скан)." };
    }
    const covered = [], missing = [];
    [...req].sort().forEach(t => {
      if (have.has(t) || haveJoin.includes(t) || haveArr.some(h => h.includes(t) || t.includes(h))) covered.push(t);
      else missing.push(t);
    });
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

  async function readDocx(file) {
    const buf = new Uint8Array(await file.arrayBuffer());
    const dv = new DataView(buf.buffer);
    let i = 0;
    while (i < buf.length - 4) {
      if (buf[i] === 0x50 && buf[i + 1] === 0x4b && buf[i + 2] === 0x03 && buf[i + 3] === 0x04) {
        const method = dv.getUint16(i + 8, true);
        let compSize = dv.getUint32(i + 18, true);
        const nameLen = dv.getUint16(i + 26, true);
        const extraLen = dv.getUint16(i + 28, true);
        const fname = new TextDecoder().decode(buf.slice(i + 30, i + 30 + nameLen));
        const dataStart = i + 30 + nameLen + extraLen;
        if (fname === "word/document.xml") {
          const data = buf.slice(dataStart, dataStart + (compSize || buf.length - dataStart));
          let xmlBytes;
          if (method === 0) xmlBytes = data;
          else {
            const ds = new DecompressionStream("deflate-raw");
            const stream = new Blob([data]).stream().pipeThrough(ds);
            xmlBytes = new Uint8Array(await new Response(stream).arrayBuffer());
          }
          let xml = new TextDecoder("utf-8").decode(xmlBytes);
          xml = xml.replace(/<\/w:p>/g, "\n").replace(/<[^>]+>/g, "");
          return xml.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'");
        }
        i = dataStart + (compSize || 0) + 1;
        if (!compSize) i = dataStart; // на всякий случай двигаемся вперёд
      } else i++;
    }
    throw new Error("Не удалось прочитать .docx — вставьте текст вручную");
  }

  return { compare, extractText };
})();
