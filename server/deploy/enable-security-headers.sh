#!/usr/bin/env bash
# Заголовки безопасности nginx для Лекало — ЗАПУСКАТЬ НА VPS (root). Идемпотентно.
# Кладёт сниппет в /etc/nginx/snippets и подключает его include'ом в КАЖДЫЙ
# server-блок живого конфига (голый IP + nip.io). Повторный запуск не дублирует.
#
# Запуск:  cd /opt/lekalo/server/deploy && bash enable-security-headers.sh
set -euo pipefail

SITE="/etc/nginx/sites-available/lekalo"
ENABLED="/etc/nginx/sites-enabled/lekalo"
SNIPPET="/etc/nginx/snippets/lekalo-security.conf"
HERE="$(cd "$(dirname "$0")" && pwd)"

[ -f "$SITE" ] || { echo "НЕТ $SITE — сперва деплой сайта (DEPLOY.md шаг 5)"; exit 1; }

# sites-enabled/lekalo ДОЛЖЕН быть симлинком на $SITE, иначе правки уходят в
# никуда: nginx грузит sites-enabled, а не sites-available (грабля §7).
if [ ! -L "$ENABLED" ] || [ "$(readlink -f "$ENABLED")" != "$(readlink -f "$SITE")" ]; then
  echo "!! $ENABLED — не симлинк на $SITE (разошлись) — чиню"
  mkdir -p /root/nginx-backups
  [ -e "$ENABLED" ] && cp "$ENABLED" "/root/nginx-backups/lekalo-enabled-$(date +%s).bak" || true
  rm -f "$ENABLED"
  ln -s "$SITE" "$ENABLED"
  echo "-- симлинок восстановлен: $ENABLED -> $SITE --"
fi

# бэкап конфига перед правкой
cp "$SITE" "/root/nginx-backups/lekalo-$(date +%s).bak" 2>/dev/null || { mkdir -p /root/nginx-backups; cp "$SITE" "/root/nginx-backups/lekalo-$(date +%s).bak"; }

# 1) сниппет с заголовками (из версии в репо)
mkdir -p /etc/nginx/snippets
cp "$HERE/nginx-lekalo-security.conf" "$SNIPPET"
echo "-- сниппет установлен: $SNIPPET --"

# 2) include в каждый server-блок. Идемпотентность через удаление прежних
# включений перед вставкой — повторный запуск даёт ровно одну строку на блок.
sed -i '\#include snippets/lekalo-security.conf;#d' "$SITE"
sed -i '/^[[:space:]]*server[[:space:]]*{/a\    include snippets/lekalo-security.conf;' "$SITE"
echo "-- include добавлен в server-блоков: $(grep -c 'include snippets/lekalo-security.conf;' "$SITE") --"

nginx -t && systemctl reload nginx
echo "✔ ГОТОВО: заголовки безопасности активны (X-Frame-Options, CSP, nosniff, Referrer-Policy, server_tokens off)."
echo "   Проверка: curl -sI https://147.45.141.237.nip.io/ | grep -iE 'x-frame|content-security|x-content-type|referrer'"
