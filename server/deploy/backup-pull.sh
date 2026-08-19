#!/usr/bin/env bash
# Забрать последний шифрованный бэкап БД с VPS на ЭТУ (основную/домашнюю) машину —
# смысл off-site: если VPS/диск умрёт, копия аккаунтов останется здесь.
# ЗАПУСКАТЬ НА ОСНОВНОЙ машине (там SSH-ключ к VPS). По расписанию — Планировщик Windows.
#
# Разовая проверка (Git Bash):  bash server/deploy/backup-pull.sh
# Планировщик (ежедневно 04:00, после серверного бэкапа в 03:00) — запускать из
# PowerShell, не Git Bash (тот перепишет "/TN" и т.п. в путь вида "C:/Program
# Files/Git/TN" — известная особенность MSYS2; в Git Bash нужен
# MSYS2_ARG_CONV_EXCL="*", иначе тот же эффект):
#   schtasks /Create /TN LekaloDbBackupPull /SC DAILY /ST 04:00 /F ^
#     /TR "\"C:\Program Files\Git\bin\bash.exe\" -lc \"bash /c/Users/user/TenderPlan-COPY/server/deploy/backup-pull.sh\""
set -euo pipefail

KEY_SSH="${LK_SSH_KEY:-$HOME/.ssh/nexara_deploy}"
HOST="${LK_VPS:-root@147.45.141.237}"
DEST="${LK_BACKUP_DEST:-$HOME/lekalo-backups}"
KEEP="${LK_BACKUP_KEEP_LOCAL:-30}"

mkdir -p "$DEST"
# самый свежий .enc на сервере
LATEST=$(ssh -i "$KEY_SSH" -o StrictHostKeyChecking=accept-new "$HOST" \
  "ls -1t /root/backups/lekalo-db-*.sqlite.gz.enc 2>/dev/null | head -1" || true)
[ -n "$LATEST" ] || { echo "на VPS нет бэкапов (запущен ли backup-db.sh по cron?)"; exit 1; }

scp -i "$KEY_SSH" "$HOST:$LATEST" "$DEST/"
echo "✔ забрал $(basename "$LATEST") → $DEST"

# локальная ротация: держать последние $KEEP
ls -1t "$DEST"/lekalo-db-*.sqlite.gz.enc 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f
