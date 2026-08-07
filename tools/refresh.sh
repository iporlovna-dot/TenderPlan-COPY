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

# Отпечаток снапшота: не только СОСТАВ закупок, но и появились ли у них документы.
# Документы ЕИС накапливаются между прогонами (кэш в tools/.cache) — если сверять
# только id, прогон, добывший ТЗ для сотни закупок без изменения состава, считался
# бы «ничего не поменялось», и покрытие никогда бы не доехало до прода.
ids() { node -e 'try{console.log(require("./site/data/purchases.json").purchases.map(p=>p.id+":"+((p.documents||[]).length?1:0)).sort().join(","))}catch(e){console.log("")}'; }

before="$(ids)"
node tools/build_snapshot.js
after="$(ids)"

# Аналитика (реестр контрактов) — история контрактов почти не меняется от часа к
# часу, пересобирать её на каждый прогон (доп. ~3000 запросов к источнику) смысла
# нет. Пересобираем максимум раз в LK_ANALYTICS_MAX_AGE_HOURS часов (по умолчанию
# 20 — примерно раз в сутки), деплоим то, что уже есть, при каждом прогоне (это
# просто scp готового файла, дёшево).
ANALYTICS_MAX_AGE_HOURS="${LK_ANALYTICS_MAX_AGE_HOURS:-20}"
analytics_stale() {
  node -e '
    const fs = require("fs");
    try {
      const d = JSON.parse(fs.readFileSync("site/data/analytics.json", "utf8"));
      const ageH = (Date.now() - new Date(d.generatedAt).getTime()) / 3600000;
      process.exit(ageH > Number(process.argv[1]) ? 0 : 1);
    } catch (e) { process.exit(0); }
  ' "$ANALYTICS_MAX_AGE_HOURS"
}
if analytics_stale; then
  echo "$(date +%F\ %H:%M) аналитика устарела/отсутствует — пересобираю"
  node tools/build_analytics.js || echo "$(date +%F\ %H:%M) ОШИБКА сборки аналитики — оставляю прежний файл"
fi

if [ "$before" = "$after" ]; then
  echo "$(date +%F\ %H:%M) состав закупок не изменился — покупки не деплою"
else
  # scp во временный файл + атомарный mv на сервере — чтобы nginx никогда не
  # отдал наполовину записанный JSON.
  if scp -i "$KEY" -o BatchMode=yes -o ConnectTimeout=20 \
       site/data/purchases.json "$VPS:$REMOTE_DIR/purchases.json.tmp" \
     && ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=20 \
       "$VPS" "mv $REMOTE_DIR/purchases.json.tmp $REMOTE_DIR/purchases.json"
  then
    echo "$(date +%F\ %H:%M) закупки обновлены и задеплоены по scp (состав изменился)"
  else
    echo "$(date +%F\ %H:%M) ОШИБКА доставки закупок по scp — на VPS остался прежний снапшот"
  fi
fi

if [ -f site/data/analytics.json ]; then
  if scp -i "$KEY" -o BatchMode=yes -o ConnectTimeout=20 \
       site/data/analytics.json "$VPS:$REMOTE_DIR/analytics.json.tmp" \
     && ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=20 \
       "$VPS" "mv $REMOTE_DIR/analytics.json.tmp $REMOTE_DIR/analytics.json"
  then
    echo "$(date +%F\ %H:%M) аналитика задеплоена по scp"
  else
    echo "$(date +%F\ %H:%M) ОШИБКА доставки аналитики по scp — на VPS остался прежний файл"
  fi
fi
