// Позиции лота из таблицы КТРУ в «Описании объекта закупки» (.docx).
//
// Зачем. До сих пор из ТЗ мы забирали 120 частотных основ — мешок слов. А внутри
// «Описания объекта закупки» лежит НАСТОЯЩАЯ спецификация: таблица с позициями,
// кодами КТРУ, характеристиками, операторами сравнения и единицами. Это уже
// структура, и её не надо угадывать моделью — надо просто не выбрасывать.
//
// Замер на 25 живых документах: разобралось 14 (остальные — .pdf/.doc и ошибки
// скачивания), таблицы нашлись у ВСЕХ 14, коды КТРУ — у 8 (57%).
//
// Словарь намеренно совпадает с движком сверки (matcher/src/schema.py):
// operator = eq|gte|lte|range|present, hardness = hard|soft. Совпадение не
// случайно — в колонке «Инструкция по заполнению заявки» заказчик пишет ровно
// «значение не может изменяться» (это hard) и «участник указывает в заявке
// конкретное значение» (soft), то есть движок писался под эти же документы.

// ─────────────────────────────────────────────────────────── таблицы из XML

// Идём по тегам, а не регэкспом `<w:tbl>[\s\S]*?</w:tbl>`: таблицы в ТЗ бывают
// вложенными, и нежадный шаблон схлопнул бы внешнюю на закрытии внутренней.
function docxTables(xml) {
  const tables = [];
  const stack = [];
  const re = /<(\/?)w:(tbl|tr|tc)\b([^>]*?)>/g;
  let m, row = null, cellStart = -1;
  while ((m = re.exec(xml))) {
    const closing = m[1] === "/";
    const tag = m[2];
    if (m[3].endsWith("/")) continue;            // <w:tbl/> — пустышка, не открывает
    if (tag === "tbl") {
      if (!closing) stack.push({ rows: [] });
      else { const t = stack.pop(); if (t) tables.push(t); }
    } else if (!stack.length) {
      continue;                                   // строка/ячейка вне таблицы — мусор
    } else if (tag === "tr") {
      if (!closing) row = [];
      else if (row) { stack[stack.length - 1].rows.push(row); row = null; }
    } else if (tag === "tc") {
      if (!closing) cellStart = m.index + m[0].length;
      else if (cellStart >= 0 && row) {
        row.push(cellText(xml.slice(cellStart, m.index)));
        cellStart = -1;
      }
    }
  }
  return tables;
}

function cellText(inner) {
  return (inner.match(/<w:t\b[^>]*>([\s\S]*?)<\/w:t>/g) || [])
    .map((t) => t.replace(/^<[^>]+>/, "").replace(/<\/w:t>$/, ""))
    .join("")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'").replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
}

// ─────────────────────────────────────────────────── какая таблица — спецификация

const norm = (s) => (s || "").toLowerCase().replace(/ё/g, "е");

// Колонки называют по-разному в каждом регионе, поэтому ищем по смыслу, а не по
// точному заголовку. Порядок проверок — часть логики, и он проверен тестами в
// обе стороны:
//   • «наименование характеристики» ловим РАНЬШЕ «наименования», иначе
//     характеристика займёт колонку товара;
//   • «наименование» ловим РАНЬШЕ «кода», иначе заголовок «Наименование товара
//     по КТРУ /Наименование товара» уедет в код — слово «КТРУ» в нём есть, — и
//     колонки товара у таблицы не окажется вовсе.
// Настоящий код при этом не теряется: колонки вроде «КТРУ (при наличии)» или
// «Код ОКПД 2 / КТРУ» слов «наименование»/«товар» не содержат.
const COLUMNS = [
  ["charName", /наименование\s+характеристик|требуемый\s+параметр|^характеристик/],
  ["charValue", /значение\s+характеристик|требуемое\s+значение|^значение/],
  ["unit", /ед\.?\s*изм|единиц[аы]\s+измерения/],
  ["qty", /кол-?во|количество/],
  ["hardness", /инструкц/],
  ["name", /наименование|товар|объект\s+закупки/],
  ["code", /ктру|код\s+позиции|окпд/],
];

function mapColumns(header) {
  const map = {};
  header.forEach((cell, i) => {
    const h = norm(cell);
    if (!h) return;
    // Сильный сигнал «это колонка товара» бьёт порядок COLUMNS: комбинированные
    // шапки «Наименование товара / номер позиции в КТРУ / количество» иначе
    // уходят в qty (слово «количество» проверяется раньше «наименования») и
    // таблица теряет колонку товара — isSpecHeader не срабатывает вовсе.
    // «наименование характеристики» под это не подпадает (есть «характеристик»).
    if (map.name === undefined && /наименование\s+товар|объект\s+закупки|наименование\s+объекта/.test(h)
        && !/характеристик/.test(h)) {
      map.name = i; return;
    }
    for (const [key, re] of COLUMNS) {
      if (map[key] === undefined && re.test(h)) { map[key] = i; break; }
    }
  });
  return map;
}

// Спецификацию отличаем ПО ЗАГОЛОВКУ, а не по размеру. В том же документе
// попадаются таблицы на 682 строки — «Наименование оборудования | модель | инв №
// | зав. №», список техники для обслуживания. Она больше всех и выиграла бы
// любой отбор «самая длинная».
function isSpecHeader(map) {
  return map.name !== undefined && (map.code !== undefined || map.charName !== undefined);
}

function pickSpecTable(tables) {
  let best = null;
  for (const t of tables) {
    if (t.rows.length < 2) continue;
    // Шапка бывает не в первой строке (объединённый титул сверху) — пробуем первые три.
    for (const hi of [0, 1, 2]) {
      if (hi >= t.rows.length) break;
      const map = mapColumns(t.rows[hi]);
      if (!isSpecHeader(map)) continue;
      const cand = { table: t, map, headerRow: hi, body: t.rows.slice(hi + 1) };
      if (!best || cand.body.length > best.body.length) best = cand;
      break;
    }
  }
  return best;
}

// ─────────────────────────────────────────────────────────── значения и операторы

const KTRU_RE = /\b(\d{2}\.\d{2}\.\d{2}\.\d{3}-\d{8})\b/;
const OKPD_RE = /\b(\d{2}\.\d{2}\.\d{2}\.\d{3})\b/;

function num(s) {
  const t = String(s).replace(/\s+/g, "").replace(",", ".");
  const v = parseFloat(t);
  return Number.isFinite(v) ? v : null;
}

// Заказчик пишет оператор словами («не менее или равно 4») или знаком («≥ 5»).
// Встречается и то и другое, иногда в одном документе.
const GTE_WORDS = /не\s+менее|не\s+ниже|от\s+/;
const LTE_WORDS = /не\s+более|не\s+выше|не\s+превыша|до\s+/;
const NUMBER = /-?\d+(?:[.,]\d+)?/g;

function parseValue(raw) {
  const s = norm(raw).replace(/ /g, " ").replace(/[;.,\s]+$/, "").trim();
  if (!s) return null;
  // «соответствие», «наличие», «требуется» — требование есть, числа нет
  if (/^(соответстви[ея]|наличие|требуется|есть|да)$/.test(s)) {
    return { operator: "present", value: true };
  }
  const nums = (s.match(NUMBER) || []).map(num).filter((v) => v !== null);
  const hasGte = /≥|>=/.test(s) || GTE_WORDS.test(s);
  const hasLte = /≤|<=/.test(s) || LTE_WORDS.test(s);
  // «≥ 115 и ≤ 120» — диапазон. Проверяем ДО одиночных операторов.
  if (hasGte && hasLte && nums.length >= 2) {
    return { operator: "range", value: [Math.min(...nums), Math.max(...nums)] };
  }
  if (nums.length) {
    if (hasGte) return { operator: "gte", value: nums[0] };
    if (hasLte) return { operator: "lte", value: nums[0] };
    // «от 5 до 10» без слов-операторов
    if (nums.length >= 2 && /\bдо\b|—|–|\.\./.test(s)) {
      return { operator: "range", value: [Math.min(...nums), Math.max(...nums)] };
    }
    if (nums.length === 1 && /^[^а-я]*-?\d+(?:[.,]\d+)?[^а-я]*$/.test(s)) {
      return { operator: "eq", value: nums[0] };
    }
  }
  return { operator: "eq", value: String(raw).trim() };   // текстовое значение как есть
}

// hard/soft — из колонки «Инструкция по заполнению заявки». Это не украшение:
// hard означает, что значение менять нельзя, то есть несовпадение
// дисквалифицирует, а soft — что участник вписывает своё.
function parseHardness(raw) {
  const s = norm(raw);
  if (/не\s+мож[еы]т\s+измен|не\s+подлежит\s+измен|неизменн/.test(s)) return "hard";
  if (/указывает|конкретное\s+значение|в\s+заявке/.test(s)) return "soft";
  return "soft";
}

// ────────────────────────────────────────────────────────────── разбор строк

// Ключевое свойство этих таблиц: одна позиция занимает МНОГО строк. В первой
// строке — наименование и код, дальше идут характеристики с пустой колонкой
// наименования (объединённые ячейки). Поэтому текущую позицию тащим вперёд, а
// новую начинаем только там, где наименование непустое.
// Строка-нумерация столбцов («1 2 3 4 …») сразу под шапкой: часть заказчиков
// дублирует номера колонок отдельной строкой. Разбор принимал её за позицию
// (name-ячейка = «2»), плодил мусорный товар и сбивал перенос характеристик.
// Признак: три и больше непустых ячеек, все — разные короткие целые.
function isEnumerationRow(row) {
  const cells = row.map((c) => (c || "").trim()).filter(Boolean);
  if (cells.length < 3) return false;
  return cells.every((c) => /^\d{1,2}$/.test(c)) && new Set(cells).size === cells.length;
}

function parseSpecTable(spec, { maxItems = 200, maxChars = 60 } = {}) {
  const { map, body } = spec;
  const at = (row, key) => (map[key] !== undefined ? (row[map[key]] || "").trim() : "");
  const items = [];
  let cur = null;

  for (const row of body) {
    if (!row.length) continue;
    if (isEnumerationRow(row)) continue;
    const name = at(row, "name");
    const codeCell = at(row, "code") || name;
    const charName = at(row, "charName");
    const charValue = at(row, "charValue");

    const startsItem = Boolean(name) && name !== charName;
    if (startsItem) {
      if (items.length >= maxItems) break;
      cur = {
        name: name.replace(/^код\s+по\s+ктру:\s*/i, "").slice(0, 300),
        ktru: (KTRU_RE.exec(codeCell) || [])[1] || "",
        okpd: (OKPD_RE.exec(codeCell) || [])[1] || "",
        qty: num(at(row, "qty")),
        unit: at(row, "unit"),
        chars: [],
      };
      items.push(cur);
    }
    if (!cur) continue;
    // Код может прийти не колонкой, а строкой ниже: часть заказчиков вместо
    // отдельной колонки пишет прямо в характеристиках «Код по КТРУ: 32.50…».
    // Поэтому, пока код не найден, просматриваем ВСЮ строку — своей позиции она
    // принадлежит гарантированно, следующая начнёт новый cur.
    if (!cur.ktru || !cur.okpd) {
      const hay = codeCell + " " + row.join(" ");
      if (!cur.ktru) cur.ktru = (KTRU_RE.exec(hay) || [])[1] || "";
      if (!cur.okpd) cur.okpd = (OKPD_RE.exec(hay) || [])[1] || "";
    }
    if (cur.qty === null) cur.qty = num(at(row, "qty"));

    if (charName && charValue && cur.chars.length < maxChars) {
      const parsed = parseValue(charValue);
      if (parsed) {
        const numericOp = parsed.operator === "gte" || parsed.operator === "lte"
          || parsed.operator === "range";
        cur.chars.push({
          // Заказчики нумеруют характеристики внутри ячейки («1. Назначение»).
          // Номер — часть оформления, а не имени, и мешает сверке ключей.
          key: charName.replace(/^\s*\d+[.)]\s*/, "").slice(0, 120),
          operator: parsed.operator,
          value: parsed.value,
          // Единица осмысленна только при числовом сравнении. У текстовых
          // значений в этой колонке стоит единица ТОВАРА (объединённая ячейка
          // тянется вниз), и «Назначение = "Для анализаторов" Штука» — мусор.
          unit: numericOp ? (at(row, "unit") || "") : "",
          hardness: parseHardness(at(row, "hardness")),
          raw: charValue.slice(0, 160),
        });
      }
    }
  }
  // Позиция без кода и без характеристик — это, скорее всего, строка «Итого»
  // или заголовок раздела, а не товар.
  return items.filter((it) => it.name && (it.ktru || it.okpd || it.chars.length));
}

// ───────────────────────────────────────────── товарная таблица (фолбэк без КТРУ)
//
// У ~20% ТЗ без КТРУ-таблицы (замер на живых документах) есть простая товарная
// таблица: «Наименование | Кол-во | Ед.изм», без кодов и без колонки
// характеристик. Раньше такие теряли — карточка показывала мешок основ слов.
// Берём из них РЕАЛЬНЫЕ названия товаров (текст ячеек, не стеммы) как позиции без
// характеристик. Спецтаблицу (КТРУ) это не трогает: она даёт больше и берётся
// первой; товарная — фолбэк, только когда спецификации нет.

function pickGoodsTable(tables) {
  let best = null;
  for (const t of tables) {
    if (t.rows.length < 2) continue;
    for (const hi of [0, 1, 2]) {
      if (hi >= t.rows.length) break;
      const map = mapColumns(t.rows[hi]);
      if (isSpecHeader(map)) break;                 // это спецтаблица — не наш случай
      // Нужна колонка товара И хоть один товарный признак (кол-во/единица), иначе
      // под фильтр попал бы список инвентаря «Наименование оборудования | инв №».
      if (map.name === undefined || (map.qty === undefined && map.unit === undefined)) break;
      const body = t.rows.slice(hi + 1);
      if (!best || body.length > best.body.length) best = { table: t, map, headerRow: hi, body };
      break;
    }
  }
  return best;
}

// Строка товарной таблицы, которую НЕ считаем товаром: итоги, финансовые строки,
// чистые числа. «285/70 R19,5 Шина» товар — в нём есть буквы, и он проходит.
function isGoodsName(name) {
  if (!name || name.length < 3) return false;
  const n = norm(name);
  if (/^(итого|всего|ндс|цена|стоимость|сумма|№|x{1,3}$)/.test(n)) return false;
  if (!/[а-яёa-z]/i.test(n)) return false;          // ни одной буквы — не название
  return true;
}

function parseGoodsTable(spec, { maxItems = 200 } = {}) {
  const { map, body } = spec;
  const at = (row, key) => (map[key] !== undefined ? (row[map[key]] || "").trim() : "");
  const items = [];
  for (const row of body) {
    if (!row.length || isEnumerationRow(row)) continue;
    const hay = row.join(" ");
    const ktru = (KTRU_RE.exec(hay) || [])[1] || "";
    const okpd = (OKPD_RE.exec(hay) || [])[1] || "";
    // Код КТРУ/ОКПД часто вписан прямо в ячейку названия («Машина для пасты28.93.17.290»).
    // Он у нас уже есть отдельным полем — из имени убираем, чтобы не мозолил глаза.
    const name = at(row, "name").replace(KTRU_RE, "").replace(OKPD_RE, "").replace(/\s{2,}/g, " ").trim();
    if (!isGoodsName(name)) continue;
    if (items.length >= maxItems) break;
    items.push({
      name: name.slice(0, 300),
      ktru,
      okpd,
      qty: num(at(row, "qty")),
      unit: at(row, "unit"),
      chars: [],            // характеристик в товарной таблице нет — это и отличает её от КТРУ
    });
  }
  return items;
}

// ─────────────────────────────────────────────────────────────── точка входа

function extractLotItems(xml, opts) {
  const tables = docxTables(xml);
  const spec = pickSpecTable(tables);
  if (spec) {
    const items = parseSpecTable(spec, opts);
    if (items.length) return { items, status: "ok" };
    // Спецтаблица нашлась, но разбор пуст (объединённые ячейки/битая разметка) —
    // не сдаёмся молча, пробуем товарную таблицу ниже.
  }
  const goods = pickGoodsTable(tables);
  if (goods) {
    const items = parseGoodsTable(goods, opts);
    if (items.length) return { items, status: "goods" };
  }
  return { items: [], status: spec ? "empty-table" : "no-table" };
}

module.exports = {
  docxTables, cellText, mapColumns, isSpecHeader, pickSpecTable,
  parseValue, parseHardness, parseSpecTable, extractLotItems,
  pickGoodsTable, parseGoodsTable, isGoodsName, isEnumerationRow,
  KTRU_RE, OKPD_RE,
};
