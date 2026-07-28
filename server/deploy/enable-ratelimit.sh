#!/usr/bin/env bash
# Сетевые rate-лимиты nginx для Лекало — ЗАПУСКАТЬ НА VPS (root). Идемпотентно.
# 1) кладёт зоны (limit_req_zone/limit_conn_zone) в /etc/nginx/conf.d/
# 2) вставляет limit_req/limit_conn в КАЖДЫЙ 'location /api/ {' живого конфига
#    (их два: голый IP + nip.io). Повторный запуск ничего не дублирует.
#
# Запуск:  cd /opt/lekalo/server/deploy && bash enable-ratelimit.sh
set -euo pipefail

SITE="/etc/nginx/sites-available/lekalo"
DROPIN="/etc/nginx/conf.d/lekalo-limits.conf"
HERE="$(cd "$(dirname "$0")" && pwd)"

[ -f "$SITE" ] || { echo "НЕТ $SITE — сперва деплой сайта (DEPLOY.md шаг 5)"; exit 1; }

# 1) зоны в http-контекст (из версии в репо)
cp "$HERE/nginx-lekalo-limits.conf" "$DROPIN"
echo "-- зоны установлены: $DROPIN --"

# 2) директивы в каждый location /api/ (если ещё не вставлены)
if ! grep -q "limit_req zone=lekalo_api" "$SITE"; then
  sed -i '/location \/api\/ {/a\        limit_req zone=lekalo_api burst=20 nodelay;\n        limit_conn lekalo_conn 20;' "$SITE"
  echo "-- limit_req/limit_conn добавлены в location /api/ --"
else
  echo "-- лимиты в $SITE уже есть --"
fi

nginx -t && systemctl reload nginx
echo "✔ ГОТОВО: rate-лимиты активны (флуд по /api/ → 429)."
