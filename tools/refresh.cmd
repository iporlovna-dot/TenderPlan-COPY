@echo off
REM Обёртка для планировщика Windows — запускает tools/refresh.sh через Git Bash.
REM Путь к проекту и bash при необходимости поправить под свою машину.
"C:\Program Files\Git\bin\bash.exe" -lc "cd /c/Users/nikit/OneDrive/Dokumente/TenderPlan-COPY && bash tools/refresh.sh >> /c/Users/nikit/lekalo-refresh.log 2>&1"
