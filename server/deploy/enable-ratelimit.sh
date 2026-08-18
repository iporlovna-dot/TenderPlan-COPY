#!/usr/bin/env bash
# Сетевые rate-лимиты nginx для Лекало — ЗАПУСКАТЬ НА VPS (root). Идемпотентно.
# 1) кладёт зоны (limit_req_zone/limit_conn_zone) в /etc/nginx/conf.d/
# 2) вставляет limit_req/limit_conn в КАЖДЫЙ 'location /api/ {' живого конфига
#    (их два: голый IP + nip.io). Повторный запуск ничего не дублирует.
#
# Запуск:  cd /opt/lekalo/server/deploy && bash enable-ratelimit.sh
set -euo pipefail

SITE="/etc/nginx/sites-available/lekalo"
ENABLED="/etc/nginx/sites-enabled/lekalo"
DROPIN="/etc/nginx/conf.d/lekalo-limits.conf"
HERE="$(cd "$(dirname "$0")" && pwd)"

[ -f "$SITE" ] || { echo "НЕТ $SITE — сперва деплой сайта (DEPLOY.md шаг 5)"; exit 1; }

# sites-enabled/lekalo ДОЛЖЕН быть симлинком на $SITE, иначе правки сюда уходят
# в никуда: nginx грузит sites-enabled, а не sites-available (было 2026-07-28 —
# скрипт молча правил не тот файл, nginx -t/reload проходили, лимит не работал).
if [ ! -L "$ENABLED" ] || [ "$(readlink -f "$ENABLED")" != "$(readlink -f "$SITE")" ]; then
  echo "!! $ENABLED — не симлинк на $SITE (разошлись) — чиню"
  [ -e "$ENABLED" ] && cp "$ENABLED" "/root/nginx-backups/lekalo-enabled-$(date +%s).bak" 2>/dev/null \
    || { mkdir -p /root/nginx-backups; cp "$ENABLED" "/root/nginx-backups/lekalo-enabled-$(date +%s).bak" 2>/dev/null; }
  rm -f "$ENABLED"
  ln -s "$SITE" "$ENABLED"
  echo "-- симлинок восстановлен: $ENABLED -> $SITE --"
fi

# 1) зоны в http-контекст (из версии в репо)
cp "$HERE/nginx-lekalo-limits.conf" "$DROPIN"
echo "-- зоны установлены: $DROPIN --"

# 2) директивы в КАЖДЫЙ location /api/, где их ещё нет.
# ⚠️ Проверка «есть ли limit_req где-нибудь в файле» была неверной: блоков два —
# голый IP (лимиты лежат прямо в nginx-lekalo.conf) и nip.io, который дописывает
# enable-https.sh УЖЕ ПОСЛЕ. Файл-широкий grep находил лимит в первом блоке,
# скрипт рапортовал «уже есть» и уходил, оставляя HTTPS-блок — тот самый, через
# который ходят живые люди, — вовсе без лимита.
# index() вместо регэкспа: в пути /api/ иначе пришлось бы экранировать слэши,
# а этот awk живёт внутри sh-строки — лишний слой экранирования тут только
# источник тихих поломок.
awk '
  index($0, "location /api/ {") {
    print
    if ((getline nxt) > 0) {
      if (index(nxt, "limit_req zone=lekalo_api") == 0) {
        print "        limit_req zone=lekalo_api burst=20 nodelay;"
        print "        limit_conn lekalo_conn 20;"
        added++
      }
      print nxt
    }
    next
  }
  { print }
  END { print "-- блоков /api/ дополнено лимитами: " added+0 " --" > "/dev/stderr" }
' "$SITE" > "$SITE.tmp" && mv "$SITE.tmp" "$SITE"

nginx -t && systemctl reload nginx
echo "✔ ГОТОВО: rate-лимиты активны (флуд по /api/ → 429)."
