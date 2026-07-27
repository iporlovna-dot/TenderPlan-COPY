#!/usr/bin/env bash
# HTTPS для Лекало через Let's Encrypt — ЗАПУСКАТЬ НА VPS (root).
# Голый IP сертификат не получит, поэтому используем nip.io: <ip>.nip.io сам
# резолвится в этот IP, а LE выдаёт на него cert по HTTP-01. Позже, когда будет
# свой домен, — просто перезапустить с LK_DOMAIN=твойдомен.ru.
#
# Запуск:  bash enable-https.sh
# Свой домен:  LK_DOMAIN=lekalo.ru LK_LE_EMAIL=me@mail.ru bash enable-https.sh
#   (для своего домена сперва прописать A-запись → 186.246.30.213)
set -euo pipefail

IP="${LK_IP:-186.246.30.213}"
DOMAIN="${LK_DOMAIN:-${IP}.nip.io}"          # nip.io: 186.246.30.213.nip.io → IP
EMAIL="${LK_LE_EMAIL:-olegshakhov157@gmail.com}"   # для писем LE об истечении
SITE="/etc/nginx/sites-available/lekalo"

echo "== HTTPS для домена: ${DOMAIN} (email ${EMAIL}) =="

# 0) sanity: nginx-конфиг Лекало на месте
[ -f "$SITE" ] || { echo "НЕТ $SITE — сперва деплой по DEPLOY.md (шаг 5)"; exit 1; }

# 1) certbot + nginx-плагин
if ! command -v certbot >/dev/null 2>&1; then
  echo "-- ставлю certbot --"
  apt-get update -qq
  apt-get install -y -qq certbot python3-certbot-nginx
fi

# 2) server_name → домен (certbot --nginx ищет server-блок по имени)
if grep -qE 'server_name\s+_\s*;' "$SITE"; then
  sed -i "s/server_name\s\+_\s*;/server_name ${DOMAIN};/" "$SITE"
  echo "-- server_name → ${DOMAIN} --"
elif ! grep -q "server_name ${DOMAIN};" "$SITE"; then
  sed -i "s/\(server_name\)[^;]*;/\1 ${DOMAIN};/" "$SITE"
  echo "-- server_name перезаписан на ${DOMAIN} --"
fi
nginx -t && systemctl reload nginx

# 3) открыть 80/443, если активен ufw (иначе HTTP-01 и HTTPS не пройдут)
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow 80/tcp  >/dev/null 2>&1 || true
  ufw allow 443/tcp >/dev/null 2>&1 || true
  echo "-- ufw: 80/443 открыты --"
fi

# 4) выпустить сертификат и включить редирект HTTP→HTTPS (certbot сам правит nginx)
certbot --nginx -d "$DOMAIN" \
  --non-interactive --agree-tos -m "$EMAIL" --redirect --keep-until-expiring

# 5) проверить, что автопродление настроено (certbot ставит systemd-timer)
echo "== проверка автопродления =="
certbot renew --dry-run

echo
echo "✔ ГОТОВО. Открой:  https://${DOMAIN}/"
echo "  Сертификат продлевается автоматически (systemctl list-timers | grep certbot)."
echo "  Свой домен позже:  LK_DOMAIN=твойдомен.ru bash enable-https.sh"
