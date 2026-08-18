// Восстановить кэш карточек ЕИС из готового снапшота.
//
// Зачем. Кэши сборщика (`tools/.cache/`) машинно-специфичны и в git не лежат:
// документы копятся неделями по LK_EIS_DOCS за прогон. На ВТОРОЙ машине кэша
// нет, поэтому холодный прогон соберёт документы только для полутора сотен
// закупок — и `refresh.sh`, увидев смену состава, бодро заменит богатый
// прод-снапшот бедным. Проверено 2026-08-10: без восстановления было бы 832
// закупки с документами против 922 на проде, с восстановлением — 1123.
//
// Данные берём из самого снапшота: в нём у каждой закупки уже лежат
// `documents`/`region`/`deliveryDays` — ровно то, что кэш и хранит. Источник тот
// же самый, просто уже добытый.
//
// Использование (снапшот можно скачать с прода):
//   curl -k -o /tmp/prod.json https://104.171.137.131.nip.io/data/purchases.json
//   node tools/seed_docs_cache.js /tmp/prod.json
//
// Существующий кэш не трогаем: перезаписать накопленное хуже, чем ничего не делать.

const fs = require("fs");
const path = require("path");

const CACHE_DIR = path.resolve(__dirname, ".cache");
const DOCS_CACHE = path.join(CACHE_DIR, "eis-docs.json");

function main() {
  const src = process.argv[2];
  if (!src) {
    console.error("Укажите снапшот: node tools/seed_docs_cache.js <purchases.json>");
    process.exit(1);
  }
  if (fs.existsSync(DOCS_CACHE)) {
    console.error(`Кэш уже есть — не трогаю: ${DOCS_CACHE}`);
    console.error("Если нужно пересобрать с нуля, удалите файл вручную.");
    process.exit(1);
  }

  const snap = JSON.parse(fs.readFileSync(src, "utf8"));
  const purchases = snap.purchases || snap;
  const now = Date.now();

  const cache = {};
  let skipped = 0;
  for (const p of purchases) {
    if (!String(p.id || "").startsWith("eis_") || !p.number) continue;
    const docs = p.documents || [];
    // Пустой documents[] у ЕИС значит разное: «в карточку не заходили» (tzStatus
    // = pending) или «приложений действительно нет». Первое в кэш класть нельзя —
    // иначе соврём сборщику, что закупка уже проверена, и он туда не сходит уже
    // никогда (см. docsFetched в eis.js).
    if (!docs.length && p.tzStatus === "pending") { skipped++; continue; }
    cache[p.number] = {
      docs,
      region: p.region || "",
      deliveryDays: p.deliveryDays ?? null,
      // Ставим «сейчас»: TTL 7 дней, перепроверять их прямо сейчас незачем —
      // бюджет прогона полезнее потратить на закупки, которых в кэше нет вовсе.
      fetchedAt: now,
    };
  }

  fs.mkdirSync(CACHE_DIR, { recursive: true });
  fs.writeFileSync(DOCS_CACHE, JSON.stringify(cache), "utf8");
  const withDocs = Object.values(cache).filter(v => v.docs.length).length;
  console.log(`восстановлено записей: ${Object.keys(cache).length} (с документами ${withDocs})`);
  console.log(`пропущено «в карточку не заходили»: ${skipped}`);
  console.log(`-> ${DOCS_CACHE}`);
}

main();
