@echo off
REM Обёртка для планировщика Windows — запускает tools/refresh.sh через Git Bash.
REM Путь к проекту и bash при необходимости поправить под свою машину.
REM LK_CHROME_PATH обязателен: прицельный поиск ЕИС идёт через headless-Chromium
REM (источник глушит curl), а playwright-core своего браузера не тянет — нужен
REM системный Chrome. Портативный Node — в PATH, иначе Планировщик его не видит.
set "PATH=%USERPROFILE%\.local\node;%PATH%"
set "LK_CHROME_PATH=C:\Users\nikit\AppData\Local\Google\Chrome\Application\chrome.exe"
REM Подняты вместе (2026-08-27) с 900 после подтверждённого замера: API ЕИС реально
REM работает (481/900 обогащено официальным API за прогон), ~4 записи/сек на фазу.
REM Держим EIS_DOCS и TZ_DOCS вровень — раскачивать один без другого бессмысленно,
REM см. CLAUDE.md «Приток нельзя разгонять в отрыве от LK_EIS_DOCS».
set "LK_EIS_DOCS=6000"
set "LK_TZ_DOCS=6000"
"C:\Program Files\Git\bin\bash.exe" -lc "cd /c/Users/nikit/OneDrive/Dokumente/TenderPlan-COPY && bash tools/refresh.sh >> /c/Users/nikit/lekalo-refresh.log 2>&1"
