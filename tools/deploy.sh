#!/usr/bin/env bash
# Деплой КОДА на VPS одной командой (запускать с ОСНОВНОЙ машины — там ssh-ключ).
# Тянет свежий код из git прямо на сервере и рестартит бэкенд ТОЛЬКО если менялся
# server/ — для чистого фронта (site/**) nginx отдаёт файлы сразу, рестарт не нужен.
# Снапшот данных сюда НЕ входит (он не в git) — это делает tools/refresh.sh.
#
# Запуск:  bash tools/deploy.sh
set -euo pipefail

KEY="${LEKALO_KEY:-$HOME/.ssh/nexara_deploy}"
HOST="${LEKALO_HOST:-root@104.171.137.131}"

ssh -i "$KEY" -o ConnectTimeout=20 "$HOST" 'bash -s' <<'REMOTE'
set -e
cd /opt/lekalo
before="$(git rev-parse HEAD)"
git pull --ff-only
after="$(git rev-parse HEAD)"

if [ "$before" = "$after" ]; then
  echo "== уже свежий ($after) — деплоить нечего =="
  exit 0
fi

changed="$(git diff --name-only "$before" "$after")"
echo "== изменённые файлы =="
echo "$changed"

if echo "$changed" | grep -q '^server/app/'; then
  echo "== менялся бэкенд → systemctl restart tender-api =="
  systemctl restart tender-api
fi

# nginx-конфиг руками (enable-*.sh не идемпотентно-безопасно гонять вслепую):
if echo "$changed" | grep -Eq '^server/deploy/(nginx|enable-)'; then
  echo "!! менялся nginx-конфиг — запусти нужный server/deploy/enable-*.sh вручную (см. tools/deploy-cheatsheet.md в скилле)"
fi

echo "== задеплоено: $after =="
REMOTE
