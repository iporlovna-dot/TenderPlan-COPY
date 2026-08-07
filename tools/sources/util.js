// Общие утилиты сборщиков: асинхронный curl (с повтором при сбоях) + ограниченная
// конкурентность.

const { execFile } = require("child_process");

const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36";
const RETRIES = Number(process.env.LK_CURL_RETRIES ?? 2);
const RETRY_DELAY_MS = Number(process.env.LK_CURL_RETRY_DELAY_MS || 800);

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function curlOnce(args, binary) {
  return new Promise((resolve, reject) => {
    // -s (тихий прогресс) + -S (но ошибки всё же печатать) — раньше -s одна
    // глушила и реальный текст ошибки curl, поэтому сбои были нечитаемыми
    // ("Command failed: curl ..." без причины).
    // Accept/Accept-Language — обычный браузер их шлёт, простой curl — нет;
    // без деанонимизации, просто честный набор заголовков реального клиента.
    execFile("curl", [
      "-s", "-S", "-A", UA,
      "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "-H", "Accept-Language: ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
      "--max-time", "30", ...args,
    ],
      // encoding "buffer" — для документов (.docx это ZIP): прогон бинарника через
      // utf8-декодер необратимо портит байты, и ZIP потом не разбирается.
      { maxBuffer: 40 * 1024 * 1024, encoding: binary ? "buffer" : "utf8" },
      // node сам подставляет stderr в err.message при ненулевом коде возврата —
      // достаточно, что -S заставляет curl вообще что-то туда написать.
      (err, stdout) => (err ? reject(err) : resolve(stdout)));
  });
}

// Запуск curl асинхронно с повтором при сбоях (обрыв/таймаут/DNS) — раньше один
// неудачный запрос ронял сбор источника целиком (так уже случалось с Порталом:
// один большой запрос списка падал — и весь источник возвращал 0 закупок).
// HTTP-статусы curl сам ошибкой не считает (нет --fail) — это на совести
// вызывающего кода, повтор тут только про сетевые сбои самого curl.
async function curlAsync(args, binary) {
  let lastErr;
  for (let attempt = 0; attempt <= RETRIES; attempt++) {
    try {
      return await curlOnce(args, binary);
    } catch (e) {
      lastErr = e;
      if (attempt < RETRIES) await sleep(RETRY_DELAY_MS * (attempt + 1));
    }
  }
  throw lastErr;
}

// Скачать файл как Buffer. `-L` обязателен: ссылки на документы у обеих площадок
// ведут через редиректы (в отличие от поиска ЕИС, где -L наоборот ломает запрос —
// см. CLAUDE.md). Потолок размера — чтобы одна гигантская смета не съела память.
const MAX_DOC_BYTES = Number(process.env.LK_DOC_MAX_BYTES || 20 * 1024 * 1024);
function curlBinary(url) {
  return curlAsync(["-L", "--max-filesize", String(MAX_DOC_BYTES), url], true);
}

// Прогнать fn по массиву с не более чем `limit` одновременными запросами.
// onDone(k, total) — колбэк прогресса.
async function mapLimit(arr, limit, fn, onDone) {
  const ret = new Array(arr.length);
  let next = 0, done = 0;
  async function worker() {
    while (next < arr.length) {
      const idx = next++;
      try { ret[idx] = await fn(arr[idx], idx); } catch (e) { ret[idx] = null; }
      if (onDone) onDone(++done, arr.length);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, arr.length) }, worker));
  return ret;
}

module.exports = { curlAsync, curlBinary, mapLimit, sleep, UA };
