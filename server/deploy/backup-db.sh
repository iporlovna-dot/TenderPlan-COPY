#!/usr/bin/env bash
# Шифрованный бэкап БД Лекало — ЗАПУСКАТЬ НА VPS (root), из cron ежедневно.
# Консистентный снимок SQLite (.backup, НЕ cp живого файла) → gzip → AES-256.
#
# Ключ шифрования: /root/.lekalo_backup_key. Создать один раз:
#   openssl rand -base64 48 > /root/.lekalo_backup_key && chmod 600 /root/.lekalo_backup_key
# ⚠️ БЕЗ КЛЮЧА ВОССТАНОВИТЬ НЕЛЬЗЯ — сразу же сохрани копию ключа off-site
#   (на домашней машине, в менеджере паролей). Потеряешь ключ = потеряешь бэкапы.
#
# cron (ежедневно в 03:00):
#   0 3 * * * /opt/lekalo/server/deploy/backup-db.sh >> /var/log/lekalo-backup.log 2>&1
#
# Восстановление:
#   openssl enc -d -aes-256-cbc -pbkdf2 -pass file:/root/.lekalo_backup_key \
#     -in lekalo-db-YYYYMMDD-HHMMSS.sqlite.gz.enc | gunzip > restored.sqlite
set -euo pipefail

DB="${LK_DB_PATH:-/opt/lekalo/server/data/lekalo.db}"
DIR="/root/backups"
KEY="/root/.lekalo_backup_key"
KEEP="${LK_BACKUP_KEEP:-14}"   # сколько последних бэкапов держать на VPS

[ -f "$DB" ]  || { echo "НЕТ БД: $DB"; exit 1; }
[ -f "$KEY" ] || { echo "НЕТ ключа $KEY. Создай: openssl rand -base64 48 > $KEY && chmod 600 $KEY (и сохрани копию off-site!)"; exit 1; }
mkdir -p "$DIR"; chmod 700 "$DIR"

TS="$(date +%Y%m%d-%H%M%S)"
TMP="$(mktemp)"
OUT="$DIR/lekalo-db-$TS.sqlite.gz.enc"

# 1) консистентный снимок (безопасно даже на живой БД под нагрузкой)
sqlite3 "$DB" ".backup '$TMP'"
# 2) gzip + AES-256 (pbkdf2 + соль) → зашифрованный файл
gzip -c "$TMP" | openssl enc -aes-256-cbc -pbkdf2 -salt -pass "file:$KEY" -out "$OUT"
rm -f "$TMP"
chmod 600 "$OUT"
echo "✔ бэкап: $OUT ($(du -h "$OUT" | cut -f1))"

# 3) ротация: оставить последние $KEEP
ls -1t "$DIR"/lekalo-db-*.sqlite.gz.enc 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f
