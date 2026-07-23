// Общие утилиты сборщиков: асинхронный curl + ограниченная конкурентность.

const { execFile } = require("child_process");

const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36";

// Запуск curl асинхронно. baseArgs — массив аргументов (без "curl").
function curlAsync(args) {
  return new Promise((resolve, reject) => {
    execFile("curl", ["-s", "-A", UA, "--max-time", "30", ...args],
      { maxBuffer: 40 * 1024 * 1024, encoding: "utf8" },
      (err, stdout) => (err ? reject(err) : resolve(stdout)));
  });
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

module.exports = { curlAsync, mapLimit, UA };
