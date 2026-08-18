# Деплой на Timeweb-VPS (замена Nexara)

VPS: `104.171.137.131` (тот же, где сейчас статика Nexara). SSH-ключ
`C:/Users/user/.ssh/nexara_deploy`. **Nexara заменяем, но сперва бэкапим** —
удаление необратимо.

> ⚠️ fail2ban: если SSH не подключился с первой попытки — не долбить повторно,
> перезагрузить сервер через панель Timeweb (ключ переживает ребут, пароль — нет).

## 0. Бэкап Nexara (обязательно, до всего)

```bash
ssh -i ~/.ssh/nexara_deploy root@104.171.137.131
# на сервере:
mkdir -p /root/backups
tar czf /root/backups/nexara-$(date +%F).tgz /var/www/nexara /etc/nginx/sites-available 2>/dev/null
ls -lh /root/backups            # убедиться, что архив создан
```
Скачать бэкап к себе (по желанию):
```bash
scp -i ~/.ssh/nexara_deploy root@104.171.137.131:/root/backups/nexara-*.tgz ./
```

## 1. Зависимости на сервере

```bash
apt update && apt install -y python3-venv python3-pip nginx git
```

**Gzip для JSON/JS/CSS** — по умолчанию в Ubuntu `nginx.conf` `gzip on;` включён, но
`gzip_types` закомментирован, а без него сжимается только `text/html`. Снапшот
(`data/purchases.json`) без этого шёл несжатым — на 4000+ закупках это ~7 МБ и
заметная задержка первой загрузки ленты. Раскомментировать в `/etc/nginx/nginx.conf`
(секция `gzip`): `gzip_vary on;`, `gzip_proxied any;`, `gzip_comp_level 6;` и
`gzip_types text/plain text/css application/json application/javascript text/xml
application/xml application/xml+rss text/javascript image/svg+xml;`, затем
`nginx -t && systemctl reload nginx`. Проверка: `curl -sI -H "Accept-Encoding: gzip"
.../data/purchases.json | grep -i content-encoding` — должно быть `gzip`.

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
Открыть `http://104.171.137.131/` → лендинг Лекало, `/(app.html)` → живая лента.

## 6. Авто-обновление снапшота (опционально, если фронт статичный)

Живому API снапшот не нужен. Но если фронт крутится как статика — cron:
```bash
crontab -e
# каждые 30 минут:
*/30 * * * * cd /opt/lekalo/server && .venv/bin/python scripts/refresh_snapshot.py >> /var/log/lekalo-snapshot.log 2>&1
```

## 7. HTTPS (Let's Encrypt через nip.io — без покупки домена)

Голый IP сертификат от LE не получит. Обходим через `nip.io`: `104.171.137.131.nip.io`
сам резолвится в этот IP, а LE выдаёт cert по HTTP-01. Один скрипт делает всё
(ставит certbot, правит `server_name`, открывает 80/443, выпускает cert, включает
редирект HTTP→HTTPS, проверяет автопродление):
```bash
cd /opt/lekalo/server/deploy
bash enable-https.sh
# → https://104.171.137.131.nip.io/
```
Свой домен позже (сперва A-запись → 104.171.137.131):
```bash
LK_DOMAIN=lekalo.ru LK_LE_EMAIL=me@mail.ru bash enable-https.sh
```
Скрипт добавляет отдельный server-блок под новый домен, не трогая уже
настроенные (голый IP и nip.io остаются рабочими) — можно гонять повторно.
Продление сертификатов автоматическое (systemd-timer certbot).

## 8. Безопасность и эксплуатация

Базовое (HTTPS, secure-cookie, HSTS, CORS-замок, app-rate-limit входа) уже включено.
Ниже — то, что доводит защиту до «качественной». Всё идемпотентно.

### 8.1. Секреты в `/opt/lekalo/server/.env` (вне git)

Юнит подхватывает `EnvironmentFile=-/opt/lekalo/server/.env`. Держать там:
```ini
LK_ADMIN_USER=admin
LK_ADMIN_PASS=<длинный-случайный-пароль>          # /api/admin (HTTP Basic)
LK_TOTP_KEY=<ключ Fernet>                          # шифрование 2FA-секретов в БД
```
Ключ для 2FA сгенерировать так (один раз) и вписать в `.env`:
```bash
/opt/lekalo/server/.venv/bin/python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
systemctl restart tender-api
```
Без `LK_TOTP_KEY` 2FA продолжит работать, но секреты лежат в БД открыто (при её
утечке 2FA обходится). Уже включённые до появления ключа секреты дошифруются
автоматически при следующей перенастройке 2FA пользователем — старый plaintext
тоже читается (обратная совместимость).

### 8.2. Сетевые rate-лимиты nginx (чтобы не положили флудом)

App-лимит закрывает только вход/регистрацию/2FA. Волюметрический флуд по любому
`/api/...` режем на nginx:
```bash
cd /opt/lekalo/server/deploy && bash enable-ratelimit.sh
```
Ставит зоны в `/etc/nginx/conf.d/lekalo-limits.conf` и вписывает `limit_req`
(10 r/s, burst 20) + `limit_conn` (20/IP) в оба `location /api/`. Превышение → 429.

### 8.3. Бэкап БД off-site (единственный необратимый риск)

`lekalo.db` (аккаунты, хэши, ИНН, 2FA) существует в одном экземпляре на VPS —
сбой диска = безвозвратная потеря. Двухступенчато:

**На VPS** — создать ключ шифрования и повесить ежедневный дамп в cron:
```bash
openssl rand -base64 48 > /root/.lekalo_backup_key && chmod 600 /root/.lekalo_backup_key
#  ⚠️ СРАЗУ сохрани копию этого ключа off-site — без него бэкапы не расшифровать!
(crontab -l 2>/dev/null; echo '0 3 * * * /opt/lekalo/server/deploy/backup-db.sh >> /var/log/lekalo-backup.log 2>&1') | crontab -
bash /opt/lekalo/server/deploy/backup-db.sh   # проверить руками сейчас
```
**На основной машине** — забирать свежий бэкап к себе (Планировщик, ежедневно 04:00):
```bash
bash server/deploy/backup-pull.sh             # разовая проверка
# расписание — см. шапку backup-pull.sh (schtasks LekaloDbBackupPull)
```
Восстановление: `openssl enc -d -aes-256-cbc -pbkdf2 -pass file:/root/.lekalo_backup_key -in <файл>.enc | gunzip > restored.sqlite`.

### 8.4. Аудит-лог (детект взлома)

Вход/выход/2FA/админ-доступ пишутся в journald с префиксом `AUDIT`:
```bash
journalctl -u tender-api | grep AUDIT                 # всё
journalctl -u tender-api | grep 'AUDIT login_fail'    # подбор пароля
journalctl -u tender-api | grep 'AUDIT admin_'        # доступ к админке
```

### 8.5. Мониторинг аптайма (узнать о падении первым)

Внешний пинг (бесплатно, вне VPS — иначе не заметишь падение самого VPS):
UptimeRobot → новый HTTP(s)-монитор на `https://104.171.137.131.nip.io/api/health`,
тип «keyword», ключевое слово `ok`, интервал 5 мин, алерт на почту.

## Откат к Nexara

```bash
ln -sf /etc/nginx/sites-available/nexara /etc/nginx/sites-enabled/nexara
rm -f /etc/nginx/sites-enabled/lekalo
nginx -s reload
# при необходимости распаковать бэкап: tar xzf /root/backups/nexara-*.tgz -C /
```
