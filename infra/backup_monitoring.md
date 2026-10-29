# Бэкапы БД на 5 лет и мониторинг (KON-49)

Статус: **подготовлено в репозитории и проверено локально; на ВМ не выложено.** Команды ниже выполняет
владелец (или OpenCode по `infra/backup_install_opencode_task.md`) на `vm-re.simbirsoft`. Секреты в файлах не хранятся.
Команды помечены **[ВМ]**.

**Где что стоит (решение владельца).** Скрипты бэкапа, мониторинга, таймеры и дампы живут **только на ВМ**.
На компьютер владельца ничего из этого не устанавливается и не копируется. Единственное, что владелец делает у себя, -
вводит на ВМ логин и пароль приложения корпоративного облака (раздел 7) и, если выбрано шифрование gpg, хранит закрытый ключ; сами бэкапы к нему не попадают.

## 1. Что устанавливается

| Что | Файл | Расписание (местное время ВМ, `Europe/Samara`) |
|---|---|---|
| Дамп `app_db` и `cv_db` + ротация | `scripts/backup_db.sh`, `infra/systemd/re-backup.{service,timer}` | ежедневно 02:30 |
| Проверка восстановления во временную БД | `scripts/restore_check.sh`, `re-restore-check.*` | воскресенье 04:00 |
| Проверка `/health`, контейнеров, свежести бэкапа | `scripts/monitor_health.sh`, `re-health.*` | каждую минуту |
| Отчёт доступности пн-пт 9:00-18:00 без праздников за прошлый месяц (файл `infra/ops/holidays_ru.txt`) | `scripts/availability_report.{sh,py}`, `re-availability-report.*` | 1-го числа в 09:10 |
| Выгрузка в корпоративное облако cloud.simbirsoft1.com через rclone/WebDAV (необязательно, по умолчанию выключена) | `scripts/offsite_sync.sh`, `re-offsite.*` | ежедневно 03:30 |
| Письма с алертами и отчётом | `scripts/send_mail.py` (SMTP из `ops.env`) | вместе с алертами и отчётом |
| Перезапуск упавшего контейнера | `restart: unless-stopped` у всех сервисов в `docker-compose.yml` | Docker |

Правила хранения (A6): ежедневные дампы 30 дней; ежемесячные (первый успешный дамп месяца)
1900 дней (около 5 лет и 2 месяцев); ежемесячные не удаляются при нехватке места, вместо этого алерт
(лимит `BACKUP_MAX_GB`, по умолчанию 26 ГБ = 20% диска). Формат: `pg_dump -Fc`, рядом `.sha256`,
права 600. Дамп содержит персональные данные: храните на зашифрованном носителе, в чат и почту не отправляйте.

Дамп делается внутри контейнера `db` через `docker exec` по локальному сокету: пароли БД скриптам не нужны.
Логи скриптов - JSON в stdout (journald), без секретов (NFR-006). Журнал проверок `/health` -
`$MONITOR_DIR/health-YYYYMM.jsonl`, его нужно хранить вместе с отчётами (малый объём, около 3 МБ в месяц).

## 2. Установка на ВМ

Предусловия: приложение развёрнуто по `infra/deploy.md`, код в `/opt/re_web_presale/app`, пользователь
`user` в группе `docker`, каталог `/var/backups/re_web_presale` создан (`infra/vm_setup.md`, раздел 6),
на ВМ есть `bash`, `flock`, `curl`, `python3` (Ubuntu 24.04: есть).

**[ВМ]**
```bash
# 1. каталоги (путь бэкапов - решение владельца, см. раздел 6)
sudo mkdir -p /var/lib/re_web_presale/monitor && sudo chown user:user /var/lib/re_web_presale/monitor
chmod 700 /var/lib/re_web_presale/monitor

# 2. файл настроек (значения не печатать и не коммитить)
cp /opt/re_web_presale/app/infra/systemd/ops.env.example /opt/re_web_presale/ops.env
chmod 600 /opt/re_web_presale/ops.env
nano /opt/re_web_presale/ops.env      # BACKUP_DIR, при необходимости OFFSITE_* (раздел 7); SMTP_* и ALERT_EMAIL_TO уже заполнены в шаблоне (раздел 9)

# 3. проверить, что нет CRLF в скриптах (после доставки с Windows)
grep -lc $'\r' /opt/re_web_presale/app/scripts/*.sh /opt/re_web_presale/app/scripts/*.py /opt/re_web_presale/app/infra/systemd/* || echo "CRLF нет"
# при наличии: sed -i 's/\r$//' <файл>

# 4. ручной прогон до включения таймеров
set -a; . /opt/re_web_presale/ops.env; set +a
bash /opt/re_web_presale/app/scripts/backup_db.sh
bash /opt/re_web_presale/app/scripts/restore_check.sh
bash /opt/re_web_presale/app/scripts/monitor_health.sh
bash /opt/re_web_presale/app/scripts/offsite_sync.sh      # без OFFSITE_RCLONE_REMOTE просто пишет, что выключено
tail -n1 /var/lib/re_web_presale/monitor/health-$(date +%Y%m).jsonl
# ожидаемо: dump_ok для двух БД, restore_ok для двух БД, offsite_disabled (если облако не включено), в журнале "ok":true

# 5. включить таймеры
sudo cp /opt/re_web_presale/app/infra/systemd/re-*.service /opt/re_web_presale/app/infra/systemd/re-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now re-backup.timer re-restore-check.timer re-health.timer re-availability-report.timer
sudo systemctl enable --now re-offsite.timer     # только после настройки раздела 7; без OFFSITE_RCLONE_REMOTE он ничего не делает
systemctl list-timers 're-*' --no-pager
```

Если на ВМ предпочитают cron вместо systemd: те же скрипты, `crontab -e` под `user`
(переменные из `ops.env` экспортируйте строкой `. /opt/re_web_presale/ops.env;` перед командой):
```
30 2 * * *   set -a; . /opt/re_web_presale/ops.env; bash /opt/re_web_presale/app/scripts/backup_db.sh
0 4 * * 0    set -a; . /opt/re_web_presale/ops.env; bash /opt/re_web_presale/app/scripts/restore_check.sh
* * * * *    set -a; . /opt/re_web_presale/ops.env; bash /opt/re_web_presale/app/scripts/monitor_health.sh
10 9 1 * *   set -a; . /opt/re_web_presale/ops.env; bash /opt/re_web_presale/app/scripts/availability_report.sh
30 3 * * *   set -a; . /opt/re_web_presale/ops.env; bash /opt/re_web_presale/app/scripts/offsite_sync.sh
```
(в cron `%` экранируется; в этих строках его нет). Не включайте cron и systemd одновременно.

## 3. Как проверить восстановление

Автоматически: `re-restore-check.timer` раз в неделю; вручную:
```bash
bash /opt/re_web_presale/app/scripts/restore_check.sh                       # последние дампы обеих БД
bash /opt/re_web_presale/app/scripts/restore_check.sh /var/backups/re_web_presale/monthly/app_db_202611.dump
journalctl -u re-restore-check -n 20 --no-pager
```
Скрипт сверяет `.sha256`, создаёт `restore_check_<время>`, разворачивает дамп (`pg_restore --exit-on-error`),
печатает число таблиц и строк, сравнивает число таблиц с боевой БД и **удаляет только свою временную БД**.
Код выхода 0 и строка `restore_ok` для каждой БД - восстановление подтверждено. Боевые БД не меняются.

Для приёмки NFR-004 после выкладки: `ls -l /var/backups/re_web_presale/{daily,monthly}`; через месяц
должен появиться второй файл в `monthly/`. Глубина в 5 лет по факту наступит только через 5 лет;
проверяется конфигурация (`BACKUP_MONTHLY_DAYS`), а не срок.

## 4. Реальное восстановление (авария)

Только по решению владельца. Не использовать `docker compose down -v`.
```bash
cd /opt/re_web_presale/app
C="docker compose --env-file /opt/re_web_presale/.env -f docker-compose.yml -f /opt/re_web_presale/docker-compose.prod.yml"
bash scripts/backup_db.sh                       # свежий дамп текущего состояния (страховка)
$C stop api worker                              # остановить запись в БД
F=/var/backups/re_web_presale/daily/app_db_<метка>.dump
docker exec -i re_web_presale-db-1 pg_restore -U postgres -d app_db --clean --if-exists --exit-on-error < "$F"
$C start api worker
curl -ks https://127.0.0.1/health               # {"status":"ok"}
```
То же для `cv_db`. Предварительно проверьте нужный дамп `restore_check.sh <файл>`.

## 5. Откат установки

Данные приложения и бэкапы не затрагиваются.
```bash
sudo systemctl disable --now re-backup.timer re-restore-check.timer re-health.timer re-availability-report.timer re-offsite.timer
sudo rm /etc/systemd/system/re-*.service /etc/systemd/system/re-*.timer && sudo systemctl daemon-reload
# каталоги /var/backups/re_web_presale и /var/lib/re_web_presale/monitor с данными НЕ удалять без решения владельца
```

## 6. Мониторинг: что считается и как проверить

- `/health` раз в минуту: успех = HTTP 200 и `"status":"ok"`; запись в `health-YYYYMM.jsonl`. Алерт после
  `HEALTH_FAIL_THRESHOLD` (3) неудач подряд, один раз на инцидент (повторно - после восстановления).
- Те же прогоны проверяют, что контейнеры проекта `Up` и не `unhealthy`, и что последний успешный бэкап не старше
  36 ч. Если включена внешняя копия, проверяется и её свежесть (`OFFSITE_MAX_AGE_H`, 48 ч). `AUTOHEAL=1` перезапускает
  `unhealthy`-контейнер (раздел 10; в `ops.env.example` включено).
- Отчёт: `availability_report.sh [YYYY-MM]` - доступность = успешные минуты / все минуты окна 9:00-18:00 пн-пт;
  минуты без записи считаются недоступными; праздничные дни не исключаются (допущение A7). Результат -
  `$MONITOR_DIR/reports/availability-YYYY-MM.md` и `REPORT_CMD`, если задан.
- Падение контейнера: restart policy поднимает контейнер, завершившийся сам (проверено, раздел 12).
  **Важно:** `docker kill`/`docker stop` Docker считает ручной остановкой и не перезапускает. Для приёмочной проверки
  используйте падение процесса, а не `docker kill`. Не гонять эту проверку на боевой ВМ без согласования.


## 7. Внешняя копия в корпоративное облако (rclone, WebDAV) - необязательный шаг

По умолчанию **выключена**: пока `OFFSITE_RCLONE_REMOTE` пуст, `offsite_sync.sh` пишет `offsite_disabled` и выходит с кодом 0.
Без внешней копии потеря диска ВМ = потеря всех бэкапов (раздел 13), поэтому включить её рекомендуется.

Хранилище - корпоративное облако компании `https://cloud.simbirsoft1.com/` (решение владельца, в контуре компании).
**Допущение, не проверено:** это Nextcloud или ownCloud с доступом по WebDAV. Тип определяется шагом 7.2 до настройки.

**Что делает скрипт** (`scripts/offsite_sync.sh`, `re-offsite.timer`, ежедневно 03:30, после дампа):
1. сверяет `.sha256` выгружаемых дампов;
2. прогоняет `restore_check.sh` на последних дампах (во временную БД): в облако уходит только то, что восстанавливается
   (`OFFSITE_RESTORE_CHECK=0` отключает);
3. создаёт в облаке папку проекта `OFFSITE_REMOTE_DIR` (по умолчанию `RE_web_presale`) и в ней `daily/`, `monthly/`
   (`rclone mkdir`, при повторных запусках ничего не меняет);
4. `rclone copy` в `<remote>RE_web_presale/daily` (последний ежедневный дамп каждой БД) и `.../monthly` (все ежемесячные),
   уже выгруженное повторно не передаётся;
5. `rclone check` - выгруженное совпадает с локальным;
6. ротация в облаке теми же сроками: `daily` старше `BACKUP_DAILY_DAYS` (30), `monthly` старше `BACKUP_MONTHLY_DAYS` (1900)
   удаляются (только после успешной выгрузки);
7. при любом сбое - алерт (журнал, почта, вебхук) и код 1; успех пишется в `offsite_last_success`, и монитор
   поднимает алерт, если успешной выгрузки не было дольше `OFFSITE_MAX_AGE_H` (48 ч).
Локальные бэкапы сбой облака не затрагивает. Сразу после включения, до первой успешной выгрузки, монитор пришлёт один алерт
о «просроченной» выгрузке - это ожидаемо.

Структура в облаке:
```
RE_web_presale/
  daily/    app_db_<метка>.dump(.sha256), cv_db_<метка>.dump(.sha256)     # последний на каждую БД, хранятся 30 дней
  monthly/  app_db_YYYYMM.dump(.sha256), cv_db_YYYYMM.dump(.sha256)       # хранятся 1900 дней
```
(при `gpg` к именам добавляется `.gpg`; при `crypt` имена в облаке шифруются rclone).

Корзина облака: Nextcloud/ownCloud, вероятно, кладут удалённые ротацией файлы в свою корзину, где они занимают квоту. Проверить
после первой ротации (через 30 дней) и при необходимости очищать корзину вручную.

### 7.1. Что нужно от владельца

1. Учётная запись в `cloud.simbirsoft1.com` и **пароль приложения** (app password) для неё - владелец создаёт сам в веб-интерфейсе
   (Nextcloud: Настройки -> Безопасность -> Устройства и сеансы -> «Создать новый пароль приложения»; ownCloud: Настройки -> Безопасность ->
   Пароли приложений). Основной пароль и SSO-вход для rclone не используются. Пароль приложения - секрет: в чат, Linear, репозиторий и
   отчёты не попадает; OpenCode-агент его не видит и не вводит.
2. Решение по шифрованию (раздел 8) - до первой выгрузки.
3. Папка `RE_web_presale` в облаке создаётся скриптом; доступ к ней ограничить владельцем (не делиться ссылками и группами).
4. Облако должно вмещать бэкапы за 5 лет (оценка объёма - после первого реального дампа) и принимать файлы такого размера
   (лимит загрузки на сервере неизвестен: если дамп не проходит, это видно по ошибке `rclone copy` в алерте).

### 7.2. Пошаговая настройка rclone на ВМ (WebDAV)

**[ВМ]** установка и определение типа облака (запросы без логина, только чтение):
```bash
sudo apt install -y rclone gnupg       # Ubuntu 24.04: rclone 1.60+, этого достаточно
rclone version | head -1
curl -s https://cloud.simbirsoft1.com/status.php; echo
curl -si -X PROPFIND https://cloud.simbirsoft1.com/remote.php/dav | head -n 12
```
Как читать ответ:
- `status.php` вернул JSON с `"installed":true` и (обычно) `"productname"`/`"version"` -> Nextcloud или ownCloud; по `productname`
  (если поле есть) выбирается `vendor`: `nextcloud` или `owncloud`;
- `/remote.php/dav` ответил `401` с `WWW-Authenticate` (Basic/Bearer) -> конечная точка WebDAV существует, без логина большего не видно;
- 404, HTML-страница входа без JSON, иная платформа (не Nextcloud/ownCloud), либо ответ непонятен -> **СТОП, вопрос владельцу**; remote не создавать,
  значения не подбирать.

Если тип подтверждён, **владелец сам** вводит логин и пароль приложения в мастере rclone на ВМ (пароль при вводе не отображается;
мастер сам сохраняет его в обфусцированном виде - это то же, что `rclone obscure`; обфускация не шифрование):
```bash
rclone config
# n -> имя simbircloud -> тип webdav
# url   https://cloud.simbirsoft1.com/remote.php/dav/files/<логин>/
# vendor  nextcloud (или owncloud, по результату проверки выше)
# user  <логин>
# pass  y (ввести свой пароль) -> пароль приложения вводит владелец
# bearer_token пусто -> Edit advanced config: n -> y (сохранить) -> q
chmod 600 ~/.config/rclone/rclone.conf
```
Пароль не печатается и не пишется в `ops.env`, репозиторий, историю команд (не передавать его аргументом команды), журнал.
Команду `rclone config show` не запускать (печатает конфиг, пусть и с обфусцированным паролем).

**[ВМ]** проверка:
```bash
rclone lsd simbircloud:                            # ожидаемо: список папок корня без ошибок
rclone mkdir simbircloud:RE_web_presale && echo ok
```
Включение (в `/opt/re_web_presale/ops.env`, значения не печатать): `OFFSITE_RCLONE_REMOTE=simbircloud:`, при необходимости
`OFFSITE_REMOTE_DIR` (по умолчанию `RE_web_presale`) и выбранное шифрование (раздел 8). Затем вручную `bash scripts/offsite_sync.sh` с
`ops.env` в окружении: строки `restore_checked`, `offsite_uploaded`, `done`; проверить в веб-интерфейсе облака папку `RE_web_presale`
(подкаталоги `daily` и `monthly`, файлы); после этого `sudo systemctl enable --now re-offsite.timer`.
Если `rclone check` ругается на отсутствие общего хэша (сервер WebDAV не отдаёт хэши) - вернуть текст ошибки владельцу, не отключать проверку.

## 8. РЕШЕНИЕ ВЛАДЕЛЬЦА: шифрование выгрузки

Дампы `cv_db` и `app_db` содержат **персональные данные сотрудников** (файлы CV целиком, ФИО, контакты). Хранилище находится в контуре компании,
поэтому решение за владельцем: **хранить в контуре без шифрования** (нужен `OFFSITE_ALLOW_PLAINTEXT=1`) **либо шифровать** (`gpg` или `crypt`).
Скрипт без явного решения не работает: при `OFFSITE_ENCRYPT=none` (или пусто) он отказывается выгружать, пока не задано
`OFFSITE_ALLOW_PLAINTEXT=1`. Агент и инструкция этот флаг сами не включают. В любом варианте **доступ к папке `RE_web_presale` в облаке ограничить
владельцем**.

| Вариант | `OFFSITE_ENCRYPT` | Где ключ | Плюсы / минусы |
|---|---|---|---|
| без шифрования | `none` + `OFFSITE_ALLOW_PLAINTEXT=1` | - | проще всего, восстановление без ключей; данные читаемы всем, кто имеет доступ к папке и к серверу облака (включая его администраторов) |
| rclone crypt | `crypt` | пароль и соль в `rclone.conf` **на ВМ** | защищает от доступа к облаку и его администраторов, но не от взлома ВМ. Пароль и соль сохранить в менеджере паролей: без них копии не прочитать |
| gpg | `gpg` | на ВМ только **публичный** ключ; закрытый хранится отдельно от облака и от ВМ | взлом ВМ или облака не раскрывает выгруженное; для восстановления закрытый ключ обязателен (потеря ключа = потеря копий). Файлы `.dump.gpg` |

Настройка **crypt** (**[ВМ]**): `rclone config` -> `n` -> имя `simbircloud-crypt` -> тип `crypt` -> remote `simbircloud:RE_web_presale_enc`
(каталог-обёртка, на нём rclone шифрует содержимое) -> шифровать имена файлов `standard`, имена каталогов `true` -> пароли: сгенерировать
(`g`) и сохранить в менеджер паролей (пароли в конфиге хранятся обфусцированными, это не защита). В `ops.env`:
`OFFSITE_RCLONE_REMOTE=simbircloud-crypt:` и `OFFSITE_ENCRYPT=crypt` (скрипт проверяет, что remote действительно типа crypt). Папка
проекта `RE_web_presale` создаётся внутри crypt-remote (в облаке видны зашифрованные имена внутри `RE_web_presale_enc`).

Настройка **gpg**: пару ключей создаёт владелец **у себя** (не на ВМ), закрытый ключ и его пароль - в менеджер паролей или на
отдельный носитель. На ВМ передаётся только публичный:
```bash
gpg --full-generate-key                      # [у владельца] RSA 4096 или ECC, срок по политике; запомнить ID/почту
gpg --export -a <ID> > re_backup_public.asc  # [у владельца]
# [ВМ] после копирования re_backup_public.asc на ВМ (scp)
gpg --import re_backup_public.asc && gpg --list-keys <ID>
```
В `ops.env`: `OFFSITE_ENCRYPT=gpg`, `OFFSITE_GPG_RECIPIENT=<ID или почта ключа>`. Файлы в облаке: `*.dump.gpg` и `*.dump.gpg.sha256`.
Расшифровка (на машине с закрытым ключом): `gpg -d app_db_202611.dump.gpg > app_db_202611.dump`.

Восстановление из облака: скачать файл (`rclone copy simbircloud:RE_web_presale/monthly/<файл> .` на ВМ или через веб-интерфейс облака),
расшифровать (для `crypt` расшифровка происходит при `rclone copy simbircloud-crypt:RE_web_presale/...` сама), проверить
`restore_check.sh <файл>`, затем действовать по разделу 4. Пока схема не проверена на тестовом дампе, копию рабочей не считать:
после настройки сделайте одно пробное восстановление из облака.

## 9. Почта: алерты и месячный отчёт

Отправка - `scripts/send_mail.py` (стандартная библиотека Python, `smtplib`). Значения по умолчанию в `ops.env.example`
(внутренний relay компании):

| Переменная | Значение | Примечание |
|---|---|---|
| `SMTP_HOST` | `relay01.simbirsoft` | |
| `SMTP_PORT` | `25` | |
| `SMTP_SECURITY` | `none` | без TLS; пусто = по порту (25 - `none`, 465 - `ssl`, иначе `starttls`) |
| `SMTP_STARTTLS` | пусто | `1` включает STARTTLS принудительно; для порта 25 по умолчанию выключен |
| `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_PASSWORD_FILE` | пусто | при пустом `SMTP_USER` авторизация не выполняется (`login()` не вызывается) |
| `SMTP_FROM` | `re-web-presale@simbirsoft` | **проверить, принимает ли relay этот отправитель**; остаётся настраиваемым |
| `ALERT_EMAIL_TO` | `konstantin.shakurov@simbirsoft.com` | несколько адресов - через запятую |

Если когда-нибудь понадобится сервер с авторизацией: `SMTP_USER`, пароль только в `SMTP_PASSWORD` внутри `ops.env` (права 600) либо в
отдельном файле (права 600), путь в `SMTP_PASSWORD_FILE`; в скрипты, журнал и репозиторий он не попадает. `SMTP_VERIFY_TLS=0` - для сервера
с самоподписанным сертификатом (на порту 25 без TLS не используется).

Пока `SMTP_HOST` пуст, письма не отправляются: алерты и отчёт видны в журнале (`journalctl -u 're-*'`) и в файлах `reports/`, скрипты не
падают. Вебхук (`ALERT_WEBHOOK_URL`) и `ALERT_CMD`/`REPORT_CMD` остаются как альтернативы и работают вместе с почтой, если заданы.

Что приходит письмом: алерты (`/health` недоступен, контейнер упал или `unhealthy`, бэкап или выгрузка в облако устарели, сбой
дампа, restore-check, выгрузки) - по одному письму на инцидент; месячный отчёт доступности (1-го числа в 09:10, Markdown во
вложении и в теле). Проверка доставки после установки: `python3 -I scripts/send_mail.py --subject "тест" <<< "проверка"` с
`ops.env` в окружении; ожидаемо `mail_sent`, код 0 и письмо в ящике. Если relay отклонил письмо (`mail_failed`, например отправитель
не принят) - СТОП и вопрос владельцу, настройки не подбирать.

## 10. AUTOHEAL=1 и `restart: unless-stopped`: в чём разница

- `restart: unless-stopped` (в `docker-compose.yml`): Docker сам **поднимает контейнер, который упал или завершился** (процесс
  умер, ВМ перезагрузилась). Но если контейнер **жив и при этом «завис»** (процесс работает, а проверка здоровья `healthcheck`
  показывает `unhealthy`), Docker его не трогает - так он устроен.
- `AUTOHEAL=1` (в `ops.env.example` включён): монитор раз в минуту видит такой «зависший» контейнер (`unhealthy`) и **перезапускает**
  его командой `docker restart`. Это закрывает именно тот случай, который restart policy не лечит.
- Предохранитель: не чаще `AUTOHEAL_MAX_PER_HOUR` (3) перезапусков одного контейнера в час; дальше перезапусков нет, остаётся
  алерт - значит, нужен человек. Каждый перезапуск пишется в журнал (`autoheal_restart`). Контейнер БД перезапускается так же;
  если это нежелательно, поставьте `AUTOHEAL=0` и работайте только по алертам.
- Ручные `docker stop`/`docker kill` не отменяются ни тем, ни другим и не «чинятся».

## 11. Праздники в отчёте доступности

Отчёт считает окно пн-пт 9:00-18:00 **без нерабочих праздничных дней** из файла `infra/ops/holidays_ru.txt` (формат: `YYYY-MM-DD`
в начале строки, дальше `# комментарий`; путь задаётся `HOLIDAYS_FILE`, по умолчанию файл из репозитория рядом со скриптами).
Исключённые дни перечисляются в отчёте.
- Список праздничных дней (2026 и 2027) подтверждён владельцем 29.10.2026. Файл правит человек, раз в год.
- Если в файле нет дат за год отчёта или файл не читается, отчёт всё равно строится и пишет предупреждение, что праздники не
  исключены.
- **Работающие субботы (перенесённые рабочие дни) не учитываются**: окно отчёта - только пн-пт. Сокращённые предпраздничные
  дни тоже не учитываются (окно считается целиком).

## 12. Что проверено локально, а что нет

Проверено (Docker 28.3.3, одноразовый контейнер `postgres:16` с двумя тестовыми БД, не стенд проекта; `rclone` и SMTP-сервер
заменены локальными заглушками, в сеть и на ВМ не ходили; доработка облака и SMTP от 29.10.2026 проверена локально, см. ниже):
- дамп двух БД и ежемесячная копия, `.sha256`, ротация, restore-check (исправный, усечённый, с неверной суммой), сбой `pg_dump`,
  удаление временных БД;
- `offsite_sync.sh` (29.10.2026, rclone-remote типа `local`): создаётся папка проекта `RE_web_presale` с `daily/` и `monthly/`, повторный
  запуск ничего не передаёт, ротация по `daily`/`monthly` внутри папки проекта, отказ без шифрования; свой `OFFSITE_REMOTE_DIR`;
- `send_mail.py` (29.10.2026): порт 25, пустые `SMTP_USER`/`SMTP_PASSWORD` - `login()` и STARTTLS не вызываются; письмо доходит
  до локальной заглушки без авторизации;
- `offsite_sync.sh` (ранее): выключен по умолчанию (код 0); отказ без шифрования и без `OFFSITE_ALLOW_PLAINTEXT=1`; режим `none` с
  разрешением; режим `gpg` (временный ключ: файлы в «облаке» зашифрованы, расшифровка совпала с оригиналом, сумма `.gpg.sha256`
  сошлась); режим `crypt` (проверка типа remote, отказ для не-crypt); повторный запуск ничего не передаёт; отказ при испорченной
  контрольной сумме; алерт и код 1 при сбое `copy` и `check`; ротация в «облаке» (старые daily и monthly удалены, свежие остались);
  отказ при отсутствии публичного ключа gpg;
- `send_mail.py` против локальной заглушки SMTP: письмо с кириллицей, вложение отчёта; без `SMTP_HOST` - пропуск и код 0; при
  недоступном SMTP - код 1, вызывающий скрипт не падает и пишет `alert_delivery_failed`; пароль нигде не печатается;
- `monitor_health.sh`: алерт `/health` и контейнера письмом, `AUTOHEAL` (перезапуск `unhealthy` и срабатывание лимита),
  алерт о просроченной выгрузке;
- отчёт доступности по синтетическому журналу мая 2026: праздничные будни исключены (01.05, 11.05), простой посчитан, письмо с
  вложением; отсутствующий файл и год без дат - предупреждение в отчёте;
- `bash -n` всех скриптов, `py_compile`, `scripts/check_secrets.sh`, окончания строк LF.

**Не проверено:** реальный `rclone` и облако `cloud.simbirsoft1.com` (тип облака Nextcloud/ownCloud и доступность WebDAV, пароль
приложения, `rclone check` по хэшам WebDAV, лимиты размера загрузки и квота, корзина облака, настоящий crypt); реальный relay
`relay01.simbirsoft` (доставка в `simbirsoft.com`, принимает ли он отправителя `re-web-presale@simbirsoft`, спам-фильтры); установка и срабатывание
systemd-юнитов и таймеров (в т.ч. `re-offsite.*`; `systemd-analyze verify` не прогонялся); права пользователя `user` на docker и
каталоги; `AUTOHEAL=1` на стенде ВМ; реальный объём дампов; боевая БД с ролями `app_rw`/`cv_rw`; самоподписанный TLS ВМ;
хранение 5 лет по факту.
Известная мелочь: при ротации осиротевший `.sha256` без дампа может остаться до своей даты (на работу не влияет).

## 13. Допущения

- Файлы CV лежат в `cv_db` (bytea, `cv.employee_file_content`, ADR-009), поэтому дамп БД покрывает и файлы.
- Внешнее хранилище - корпоративное облако `https://cloud.simbirsoft1.com/` через rclone/WebDAV (решение владельца, в контуре компании),
  необязательное. Допущение: это Nextcloud/ownCloud; тип проверяется шагом 7.2. Пока выгрузка не включена, копий вне ВМ нет, и потеря
  диска ВМ = потеря бэкапов.
- Рабочие часы A7 = местное время ВМ (`Europe/Samara`).
- Алерты и отчёт без `SMTP_HOST` видны только в `journalctl -u re-*` и в файлах.
- Запрет выгрузки без шифрования по умолчанию (`OFFSITE_ALLOW_PLAINTEXT`) - защитная настройка из-за персональных данных;
  решение (хранить в контуре без шифрования либо шифровать) принимает владелец, флаг ему и включать.
- Отправитель `re-web-presale@simbirsoft` и relay без авторизации и TLS - из решения владельца; приём отправителя relay-ом не проверен.
