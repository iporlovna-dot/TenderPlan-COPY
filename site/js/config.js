// Базовый URL бэкенда.
//  ""      → статический снапшот data/purchases.json (GitHub Pages, без бэкенда)
//  "/api"  → на VPS nginx проксирует /api на FastAPI (same-origin) — оставить пусто, соберётся как /api
//  "https://api.example.ru" → если API на отдельном домене
window.LK_API_BASE = "";
