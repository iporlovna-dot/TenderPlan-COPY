// Регион закупки 44-ФЗ по её номеру — без захода в карточку.
//
// Зачем. Регион лежит только в карточке («Место нахождения» заказчика), а заход
// в карточку — отдельный запрос на закупку. Ради одного поля платить запросом
// накладно: их тысячи, и тот же запрос полезнее потратить на документы другой
// закупки.
//
// Оказалось, что платить и не нужно. В 19-значном номере извещения 44-ФЗ
// цифры 3–4 — код региона заказчика. Замер по прод-снапшоту (797 закупок, у
// которых регион уже был добыт из карточек): 33 кода с пятью и более примерами
// покрывают 727 закупок с чистотой 98.2%. Примеры: «72» → г. Санкт-Петербург
// (109 из 109), «73» → г. Москва (67 из 68), «18» → Краснодарский край (61/61).
//
// Справочник не зашит в код, а СТРОИТСЯ из уже собранного: коды со временем
// добавляются, а зашитая таблица устарела бы молча. Код, которого в справочнике
// нет, честно возвращает пустую строку — вызывающий тогда идёт в карточку и тем
// самым пополняет справочник (см. collectEis).
//
// Важно: приём работает ТОЛЬКО для 44-ФЗ. У 223-ФЗ номер другой длины и другого
// устройства (11 цифр), и цифры 3–4 там не значат ничего.

// Номер извещения 44-ФЗ: ровно 19 цифр.
const NUM44 = /^\d{19}$/;

// Порог поддержки: код, встреченный один-два раза, «чист» бессодержательно.
const MIN_SAMPLES = Number(process.env.LK_REGION_MIN_SAMPLES || 5);
// Порог чистоты: ниже него код смешивает регионы и доверять ему нельзя.
const MIN_PURITY = Number(process.env.LK_REGION_MIN_PURITY || 0.8);

function regionCode(number) {
  const n = String(number || "");
  return NUM44.test(n) ? n.slice(2, 4) : "";
}

// Собрать справочник «код → регион» из закупок, у которых регион уже известен.
// Берём только 44-ФЗ с добытым из карточки регионом.
function buildRegionIndex(purchases) {
  const tally = new Map();
  for (const p of purchases || []) {
    if (!p || p.law !== "44-ФЗ" || !p.region) continue;
    // Догадка не должна учить сама себя: иначе одна ошибка размножится и
    // «чистота» кода станет самоподтверждающейся.
    if (p.regionGuessed) continue;
    const code = regionCode(p.number);
    if (!code) continue;
    if (!tally.has(code)) tally.set(code, new Map());
    const byRegion = tally.get(code);
    byRegion.set(p.region, (byRegion.get(p.region) || 0) + 1);
  }

  const index = new Map();
  for (const [code, byRegion] of tally) {
    let total = 0, best = "", bestN = 0;
    for (const [region, n] of byRegion) {
      total += n;
      if (n > bestN) { bestN = n; best = region; }
    }
    if (total >= MIN_SAMPLES && bestN / total >= MIN_PURITY) index.set(code, best);
  }
  return index;
}

// Регион по номеру или "" — если номер не 44-ФЗ-шный либо код незнаком.
function regionFromNumber(number, index) {
  const code = regionCode(number);
  return (code && index && index.get(code)) || "";
}

module.exports = { buildRegionIndex, regionFromNumber, regionCode, MIN_SAMPLES, MIN_PURITY };
