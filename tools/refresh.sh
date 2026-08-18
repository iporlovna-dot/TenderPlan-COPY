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

# Код приезжает с ДРУГОЙ машины. Правки пушатся с копии, а сборщик крутится здесь
# по расписанию и раньше всегда исполнял то, что лежало на диске с последнего
# ручного `git pull` — то есть исправления в самом сборщике до него не доезжали
# вовсе, сколько их ни пушь. Тянем сами, в начале прогона.
# --ff-only: никаких мержей и конфликтов на машине, к которой никто не подходит.
# Неудача НЕ должна ронять сбор: данные важнее синхронизации кода, поэтому
# проверка живёт внутри `if` (там set -e не срабатывает) и прогон идёт дальше.
if git rev-parse --git-dir >/dev/null 2>&1; then
  if pull_out="$(git pull --ff-only 2>&1)"; then
    case "$pull_out" in
      *"Already up to date"*|*"Already up-to-date"*) : ;;
      *) echo "$(date +%F\ %H:%M) код обновлён до $(git log --oneline -1)" ;;
    esac
  else
    echo "$(date +%F\ %H:%M) git pull не прошёл, работаю прежним кодом: $(echo "$pull_out" | tail -1)"
  fi
fi

VPS="root@104.171.137.131"
KEY="$HOME/.ssh/nexara_deploy"
REMOTE_DIR="/opt/lekalo/site/data"

# Отпечаток снапшота: не только СОСТАВ закупок, но и появились ли у них документы
# и разобранное ТЗ. И то, и другое накапливается между прогонами (кэши в
# tools/.cache) — если сверять только id, прогон, добывший документы или термины
# ТЗ для сотни закупок без изменения состава, считался бы «ничего не поменялось»,
# и покрытие никогда бы не доехало до прода.
# ⚠️ documents, tzTerms и lotItems живут теперь в довесках (tz/docs/spec.json), в
# ленте от них остались docsCount, tzStatus и specCount — по ним и считаем
# отпечаток. Смысл прежний: «добыли документы/термины/спецификацию» обязано
# считаться изменением, иначе покрытие не доедет до прода.
ids() { node -e 'try{console.log(require("./site/data/purchases.json").purchases.map(p=>p.id+":"+(p.docsCount?1:0)+":"+(p.tzStatus==="ok"?1:0)+":"+(p.specCount?1:0)).sort().join(","))}catch(e){console.log("")}'; }

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
  # Лента и довески — четыре файла, и они обязаны быть ОДНОЙ версии: свежая
  # лента поверх старого tz.json дала бы прочерки вместо процентов там, где
  # термины уже добыты, а поверх старого spec.json — «загружаем спецификацию…»,
  # которое никогда не сменится. Поэтому сначала заливаем все во временные, и
  # только потом переименовываем одной командой — окно рассогласования сжимается
  # до миллисекунд. Временный файл + mv нужен и сам по себе: иначе nginx может
  # отдать наполовину записанный JSON.
  # Список файлов ОДИН на все три места (заливка, переименование, уборка): при
  # трёх копиях добавленный довесок неминуемо забылся бы в одном из них.
  DATA_FILES="purchases.json tz.json docs.json spec.json"
  ok=1
  for f in $DATA_FILES; do
    if [ ! -f "site/data/$f" ]; then
      echo "$(date +%F\ %H:%M) нет site/data/$f — доставку отменяю"; ok=0; break
    fi
    scp -i "$KEY" -o BatchMode=yes -o ConnectTimeout=20 \
      "site/data/$f" "$VPS:$REMOTE_DIR/$f.tmp" || { ok=0; break; }
  done
  mv_cmd="cd $REMOTE_DIR"; rm_cmd="rm -f"
  for f in $DATA_FILES; do
    mv_cmd="$mv_cmd && mv $f.tmp $f"; rm_cmd="$rm_cmd $REMOTE_DIR/$f.tmp"
  done
  if [ "$ok" = 1 ] \
     && ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=20 "$VPS" "$mv_cmd"
  then
    echo "$(date +%F\ %H:%M) закупки обновлены и задеплоены по scp (состав изменился)"
  else
    echo "$(date +%F\ %H:%M) ОШИБКА доставки закупок по scp — на VPS остался прежний снапшот"
    # недоехавшие .tmp убираем, иначе следующий прогон переименует чужой огрызок
    ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=20 "$VPS" "$rm_cmd" || true
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
