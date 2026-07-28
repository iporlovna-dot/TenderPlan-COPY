#!/usr/bin/env bash
# Автообновление снапшота: пересобрать активные КС, и если СОСТАВ изменился
# (появились новые / ушли старые) — доставить на VPS напрямую по scp. Снапшот
# больше НЕ коммитится в git — на ~4000+ закупках это ~7 МБ, ежечасно это была
# бы гигабайтная история за месяцы (см. plan.md §3). Код (site/js, server/,
# tools/) по-прежнему деплоится через git — отдельно, вручную, как раньше.
# Запускается по расписанию (планировщик Windows) НА машине, где доступен mos.ru.
#
# Ручной запуск:  bash tools/refresh.sh
set -e
cd "$(dirname "$0")/.."

VPS="root@186.246.30.213"
KEY="$HOME/.ssh/nexara_deploy"
REMOTE_DIR="/opt/lekalo/site/data"

ids() { node -e 'try{console.log(require("./site/data/purchases.json").purchases.map(p=>p.id).sort().join(","))}catch(e){console.log("")}'; }

before="$(ids)"
node tools/build_snapshot.js
after="$(ids)"

if [ "$before" = "$after" ]; then
  echo "$(date +%F\ %H:%M) состав не изменился — пропускаю"
  exit 0
fi

# scp во временный файл + атомарный mv на сервере — чтобы nginx никогда не
# отдал наполовину записанный JSON.
if scp -i "$KEY" -o BatchMode=yes -o ConnectTimeout=20 \
     site/data/purchases.json "$VPS:$REMOTE_DIR/purchases.json.tmp" \
   && ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=20 \
     "$VPS" "mv $REMOTE_DIR/purchases.json.tmp $REMOTE_DIR/purchases.json"
then
  echo "$(date +%F\ %H:%M) обновлено и задеплоено по scp (состав изменился)"
else
  echo "$(date +%F\ %H:%M) ОШИБКА доставки по scp — на VPS остался прежний снапшот"
fi
