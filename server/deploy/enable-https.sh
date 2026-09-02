#!/usr/bin/env bash
# HTTPS для Лекало через Let's Encrypt — ЗАПУСКАТЬ НА VPS (root).
# Голый IP сертификат не получит, поэтому используем nip.io: <ip>.nip.io сам
# резолвится в этот IP, а LE выдаёт на него cert по HTTP-01. Позже, когда будет
# свой домен, — просто перезапустить с LK_DOMAIN=твойдомен.ru.
#
# Запуск:  bash enable-https.sh
# Свой домен:  LK_DOMAIN=lekalo.ru LK_LE_EMAIL=me@mail.ru bash enable-https.sh
#   (для своего домена сперва прописать A-запись → 185.11.134.80)
set -euo pipefail

IP="${LK_IP:-185.11.134.80}"
DOMAIN="${LK_DOMAIN:-${IP}.nip.io}"          # nip.io: 185.11.134.80.nip.io → IP
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

# 2) отдельный server-блок под домен (certbot --nginx ищет блок по server_name
# и сам дописывает туда ssl+редирект). НЕ трогаем существующие блоки (голый IP
# и уже настроенные домены остаются рабочими как есть) — только добавляем
# новый, если такого server_name ещё нет.
if ! grep -q "server_name ${DOMAIN};" "$SITE"; then
  cat >> "$SITE" <<EOF

server {
    listen 80;
    server_name ${DOMAIN};

    root /opt/lekalo/site;
    index index.html;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 60s;
        client_max_body_size 25m;
    }
}
EOF
  echo "-- добавлен server-блок для ${DOMAIN} --"
else
  echo "-- server-блок для ${DOMAIN} уже есть --"
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

# 4.5) HSTS: браузер запоминает «этот домен только по HTTPS» (защита от downgrade/sslstrip).
#       Кладём в server-блок под ${DOMAIN}, куда certbot дописал ssl. Идемпотентно.
#       На голый IP не вешаем — он намеренно живёт на HTTP (см. nginx-lekalo.conf).
if ! grep -q "Strict-Transport-Security" "$SITE"; then
  sed -i '/ssl_certificate /a\    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;' "$SITE"
  echo "-- HSTS добавлен --"
fi
nginx -t && systemctl reload nginx

# 5) проверить, что автопродление настроено (certbot ставит systemd-timer)
echo "== проверка автопродления =="
certbot renew --dry-run

echo
echo "✔ ГОТОВО. Открой:  https://${DOMAIN}/"
echo "  Сертификат продлевается автоматически (systemctl list-timers | grep certbot)."
echo "  Свой домен позже:  LK_DOMAIN=твойдомен.ru bash enable-https.sh"
