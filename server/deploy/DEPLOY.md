# Деплой на Timeweb-VPS (замена Nexara)

VPS: `186.246.30.213` (тот же, где сейчас статика Nexara). SSH-ключ
`C:/Users/nikit/.ssh/nexara_deploy`. **Nexara заменяем, но сперва бэкапим** —
удаление необратимо.

> ⚠️ fail2ban: если SSH не подключился с первой попытки — не долбить повторно,
> перезагрузить сервер через панель Timeweb (ключ переживает ребут, пароль — нет).

## 0. Бэкап Nexara (обязательно, до всего)

```bash
ssh -i ~/.ssh/nexara_deploy root@186.246.30.213
# на сервере:
mkdir -p /root/backups
tar czf /root/backups/nexara-$(date +%F).tgz /var/www/nexara /etc/nginx/sites-available 2>/dev/null
ls -lh /root/backups            # убедиться, что архив создан
```
Скачать бэкап к себе (по желанию):
```bash
scp -i ~/.ssh/nexara_deploy root@186.246.30.213:/root/backups/nexara-*.tgz ./
```

## 1. Зависимости на сервере

```bash
apt update && apt install -y python3-venv python3-pip nginx git
```

## 2. Код проекта

```bash
mkdir -p /opt/lekalo && cd /opt/lekalo
git clone https://github.com/iporlovna-dot/TenderPlan-COPY.git .
# структура: /opt/lekalo/site (фронт), /opt/lekalo/server (API)
```

## 3. Бэкенд (venv + systemd)

```bash
cd /opt/lekalo/server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# проверка вручную:
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 &
curl 'http://127.0.0.1:8000/api/health'; curl 'http://127.0.0.1:8000/api/purchases?take=3'
kill %1

cp deploy/tender-api.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now tender-api
systemctl status tender-api --no-pager
```

## 4. Фронт указывает на свой API

Отредактировать `/opt/lekalo/site/js/config.js`:
```js
window.LK_API_BASE = "";  // тот же домен → пусто = /api/... (nginx проксирует)
```
(если API на другом домене — вписать полный URL, напр. `https://api.example.ru`)

## 5. nginx (фронт + прокси на API), выключить Nexara

```bash
cp /opt/lekalo/server/deploy/nginx-lekalo.conf /etc/nginx/sites-available/lekalo
ln -sf /etc/nginx/sites-available/lekalo /etc/nginx/sites-enabled/lekalo
rm -f /etc/nginx/sites-enabled/nexara     # отключить Nexara (файл конфига остаётся в available)
nginx -t && nginx -s reload
```
Открыть `http://186.246.30.213/` → лендинг Лекало, `/(app.html)` → живая лента.

## 6. Авто-обновление снапшота (опционально, если фронт статичный)

Живому API снапшот не нужен. Но если фронт крутится как статика — cron:
```bash
crontab -e
# каждые 30 минут:
*/30 * * * * cd /opt/lekalo/server && .venv/bin/python scripts/refresh_snapshot.py >> /var/log/lekalo-snapshot.log 2>&1
```

## 7. HTTPS (Let's Encrypt через nip.io — без покупки домена)

Голый IP сертификат от LE не получит. Обходим через `nip.io`: `186.246.30.213.nip.io`
сам резолвится в этот IP, а LE выдаёт cert по HTTP-01. Один скрипт делает всё
(ставит certbot, правит `server_name`, открывает 80/443, выпускает cert, включает
редирект HTTP→HTTPS, проверяет автопродление):
```bash
cd /opt/lekalo/server/deploy
bash enable-https.sh
# → https://186.246.30.213.nip.io/
```
Свой домен позже (сперва A-запись → 186.246.30.213):
```bash
LK_DOMAIN=lekalo.ru LK_LE_EMAIL=me@mail.ru bash enable-https.sh
```
Идемпотентно — можно гонять повторно. Продление автоматическое (systemd-timer certbot).

## Откат к Nexara

```bash
ln -sf /etc/nginx/sites-available/nexara /etc/nginx/sites-enabled/nexara
rm -f /etc/nginx/sites-enabled/lekalo
nginx -s reload
# при необходимости распаковать бэкап: tar xzf /root/backups/nexara-*.tgz -C /
```
