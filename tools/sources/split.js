// Разделение снапшота на ленту и тяжёлые довески.
//
// Зачем. Снапшот был одним файлом, и фронт грузил его целиком, чтобы показать
// ленту. Замер 6000 закупок (без отступов, 15.2 МБ): tzTerms — 23% веса,
// documents — 21%, lots — 12.5%. Итого 56% байтов уходило на то, что при
// открытии ленты не нужно ВООБЩЕ: термины читает только «Умная сверка»
// (тумблер), документы — только карточка, а lots у 97% закупок дословно
// повторяют название.
//
// Отсюда потолок LK_SNAPSHOT_MAX: накопитель рос, а в ленту уезжали ближайшие
// 6000, потому что больше браузер не разбирал. Разделение снимает не «немного
// веса», а именно этот потолок.
//
// Формат довесков — карта по id, а не массив: фронт подмешивает их обратно в
// объекты закупок, и порядок в ленте задаётся лентой, а не файлом довеска.

const FEED_V = 2;

// Поля, которые в ленте не нужны и уезжают в довески.
const TZ_FIELDS = ["tzTerms", "tzDoc", "lotItems"];
const DOC_FIELDS = ["documents"];

// «Вырожденный лот» — единственная позиция, дословно повторяющая название
// закупки и не несущая ничего сверх него. У ЕИС такой лот синтезирует сам
// адаптер (lots: [{name: title, price}]), и хранить его — значит второй раз
// платить за название. Фронт восстанавливает его из title/price.
//
// Условия нарочно строгие: у Портала поставщиков лоты настоящие (количество,
// единица, ОКПД по позиции), и потерять их нельзя.
function isDegenerateLots(p) {
  if (!Array.isArray(p.lots) || p.lots.length !== 1) return false;
  const l = p.lots[0];
  if (!l || l.name !== p.title) return false;
  if (l.okpd) return false;
  if (l.unit) return false;
  if (l.qty && l.qty !== "—") return false;
  return !l.price || l.price === p.price;
}

function synthLot(p) {
  return { name: p.title, qty: "—", unit: "", price: p.price };
}

function splitSnapshot(purchases) {
  const feed = [];
  const tz = {};
  const docs = {};

  for (const p of purchases) {
    const lite = {};
    for (const [k, v] of Object.entries(p)) {
      if (TZ_FIELDS.includes(k) || DOC_FIELDS.includes(k)) continue;
      lite[k] = v;
    }
    // Признак «документы есть» обязан остаться в ленте: без него пустой
    // documents[] неотличим от «ещё не загрузили довесок», и карточка сказала
    // бы «приложений нет» там, где мы просто не дочитали файл.
    lite.docsCount = (p.documents || []).length;
    if (isDegenerateLots(p)) delete lite.lots;

    feed.push(lite);

    if ((p.tzTerms && p.tzTerms.length) || p.tzDoc || (p.lotItems && p.lotItems.length)) {
      const e = { terms: p.tzTerms || [], doc: p.tzDoc || "" };
      // Спецификация из таблицы КТРУ есть далеко не у всех — пустое поле не пишем,
      // иначе довесок раздуется скобками на ровном месте.
      if (p.lotItems && p.lotItems.length) e.items = p.lotItems;
      tz[p.id] = e;
    }
    if (p.documents && p.documents.length) docs[p.id] = p.documents;
  }

  return { feed, tz, docs };
}

// Обратная операция — ею пользуются тесты и фронт (там своя копия на JS
// браузера). Держим рядом, чтобы разъезд формата ловился тестом, а не в проде.
function mergeSidecars(feed, tz, docs) {
  for (const p of feed) {
    const t = tz && tz[p.id];
    if (t) {
      p.tzTerms = t.terms || [];
      if (t.doc) p.tzDoc = t.doc;
      if (t.items) p.lotItems = t.items;
    }
    const d = docs && docs[p.id];
    if (d) p.documents = d;
    if (!p.documents) p.documents = [];
    if (!p.lots) p.lots = [synthLot(p)];
  }
  return feed;
}

module.exports = { FEED_V, splitSnapshot, mergeSidecars, isDegenerateLots, synthLot };
