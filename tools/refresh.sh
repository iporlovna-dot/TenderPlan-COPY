#!/usr/bin/env bash
# Автообновление снапшота: пересобрать активные КС, и если СОСТАВ изменился
# (появились новые / ушли старые) — закоммитить, запушить и подтянуть на VPS.
# Запускается по расписанию (планировщик Windows) НА машине, где доступен mos.ru.
#
# Ручной запуск:  bash tools/refresh.sh
set -e
cd "$(dirname "$0")/.."

ids() { node -e 'try{console.log(require("./site/data/purchases.json").purchases.map(p=>p.id).sort().join(","))}catch(e){console.log("")}'; }

before="$(ids)"
node tools/build_snapshot.js
after="$(ids)"

if [ "$before" = "$after" ]; then
  echo "$(date +%F\ %H:%M) состав не изменился — пропускаю"
  git checkout -- site/data/purchases.json 2>/dev/null || true   # откатить смену generatedAt
  exit 0
fi

git add site/data/purchases.json
git commit -q -m "auto: refresh snapshot"
git pull --rebase --autostash -q origin main || true
git push -q origin main
ssh -i ~/.ssh/nexara_deploy -o BatchMode=yes -o ConnectTimeout=20 \
    root@186.246.30.213 'cd /opt/lekalo && git pull --ff-only' >/dev/null 2>&1 || true
echo "$(date +%F\ %H:%M) обновлено и задеплоено (состав изменился)"
