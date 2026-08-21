// Базовый URL бэкенда.
//  ""      → статический снапшот data/purchases.json (GitHub Pages, без бэкенда)
//  "/api"  → на VPS nginx проксирует /api на FastAPI (same-origin) — оставить пусто, соберётся как /api
//  "https://api.example.ru" → если API на отдельном домене
window.LK_API_BASE = "";

// Username Telegram-бота (без @) — для ссылок на демо/тарифы/поддержку
// (server/app/support.py, ссылки в appview.js/register.html/account.html).
window.LK_BOT_USERNAME = "Bot_Lekalo_bot";
