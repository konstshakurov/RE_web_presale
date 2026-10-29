# Бэкапы БД на 5 лет и мониторинг (KON-49)

Статус: **подготовлено в репозитории и проверено локально; на ВМ не выложено.** Команды ниже выполняет
владелец (или OpenCode по `infra/backup_install_opencode_task.md`) на `vm-re.simbirsoft`. Секреты в файлах не хранятся.
Команды помечены **[ВМ]**.

**Где что стоит (решение владельца).** Скрипты бэкапа, мониторинга, таймеры и дампы живут **только на ВМ**.
На компьютер владельца ничего из этого не устанавливается и не копируется. Единственное, что владелец делает у себя, -
получает токен Google Диска (раздел 7) и, если выбрано шифрование gpg, хранит закрытый ключ; сами бэкапы к нему не попадают.

## 1. Что устанавливается

| Что | Файл | Расписание (местное время ВМ, `Europe/Samara`) |
|---|---|---|
| Дамп `app_db` и `cv_db` + ротация | `scripts/backup_db.sh`, `infra/systemd/re-backup.{service,timer}` | ежедневно 02:30 |
| Проверка восстановления во временную БД | `scripts/restore_check.sh`, `re-restore-check.*` | воскресенье 04:00 |
| Проверка `/health`, контейнеров, свежести бэкапа | `scripts/monitor_health.sh`, `re-health.*` | каждую минуту |
| Отчёт доступности пн-пт 9:00-18:00 без праздников за прошлый месяц (файл `infra/ops/holidays_ru.txt`) | `scripts/availability_report.{sh,py}`, `re-availability-report.*` | 1-го числа в 09:10 |
| Выгрузка в Google Диск через rclone (необязательно, по умолчанию выключена) | `scripts/offsite_sync.sh`, `re-offsite.*` | ежедневно 03:30 |
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
nano /opt/re_web_presale/ops.env      # BACKUP_DIR, SMTP_* (когда владелец назовёт хост), при необходимости OFFSITE_* (раздел 7)

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


## 7. Внешняя копия в Google Диск (rclone) - необязательный шаг

По умолчанию **выключена**: пока `OFFSITE_RCLONE_REMOTE` пуст, `offsite_sync.sh` пишет `offsite_disabled` и выходит с кодом 0.
Без внешней копии потеря диска ВМ = потеря всех бэкапов (раздел 13), поэтому включить её рекомендуется.

**Что делает скрипт** (`scripts/offsite_sync.sh`, `re-offsite.timer`, ежедневно 03:30, после дампа):
1. сверяет `.sha256` выгружаемых дампов;
2. прогоняет `restore_check.sh` на последних дампах (во временную БД): в облако уходит только то, что восстанавливается
   (`OFFSITE_RESTORE_CHECK=0` отключает);
3. `rclone copy` в `<remote>/daily` (последний ежедневный дамп каждой БД) и `<remote>/monthly` (все ежемесячные),
   уже выгруженное повторно не передаётся;
4. `rclone check` - выгруженное совпадает с локальным;
5. ротация в облаке теми же сроками: `daily` старше `BACKUP_DAILY_DAYS` (30), `monthly` старше `BACKUP_MONTHLY_DAYS` (1900)
   удаляются (только после успешной выгрузки);
6. при любом сбое - алерт (журнал, почта, вебхук) и код 1; успех пишется в `offsite_last_success`, и монитор
   поднимает алерт, если успешной выгрузки не было дольше `OFFSITE_MAX_AGE_H` (48 ч).
Локальные бэкапы сбой облака не затрагивает. Сразу после включения, до первой успешной выгрузки, монитор пришлёт один алерт
о «просроченной» выгрузке - это ожидаемо.

Заметка о корзине: при удалении через API Google Диск кладёт файлы в корзину (хранится 30 дней и занимает квоту). Чтобы
удалять сразу, добавьте в `ops.env`: `OFFSITE_RCLONE_FLAGS=--drive-use-trash=false`.

### 7.1. Что нужно от владельца

1. Аккаунт Google Диска и решение, **где хранить** (отдельная папка, например `re_web_presale_backups`).
2. Решение по шифрованию (раздел 8) - до первой выгрузки.
3. Токен доступа к Диску (раздел 7.2): владелец получает его сам, у себя, и вводит на ВМ лично. Токен - секрет:
   в чат, Linear, репозиторий и отчёты не попадает; OpenCode-агент его не видит и не вводит.
4. Желательно: свои `client_id`/`client_secret` из Google Cloud Console (тип OAuth «Приложение для ПК»). Иначе используется
   общий ключ rclone с жёсткими лимитами. Если создаёте своё приложение: переведите его в статус **«В производстве»**
   (In production); в режиме «Тестирование» Google отзывает токен каждые 7 дней, и выгрузка встанет.
5. Диск должен вмещать бэкапы за 5 лет (оценка объёма - после первого реального дампа).

### 7.2. Пошаговая настройка rclone на ВМ (headless, Google Drive)

**[ВМ]** установка и первый запуск мастера:
```bash
sudo apt install -y rclone gnupg       # Ubuntu 24.04: rclone 1.60+, этого достаточно
rclone version | head -1
rclone config                          # дальше отвечаем на вопросы мастера
```
В мастере: `n` (new remote) -> имя `gdrive` -> тип `drive` (Google Drive) -> `client_id` / `client_secret`: свои или пусто ->
**scope: `3` (drive.file: rclone видит только созданные им файлы)** -> `root_folder_id` пусто -> `service_account_file` пусто ->
Edit advanced config: `n` -> **Use web browser to automatically authenticate? `n`** (на ВМ нет браузера). Мастер напечатает
команду вида `rclone authorize "drive" "<длинная строка>"` и будет ждать токен.

**Компьютер владельца**: rclone нужен только чтобы получить токен. Установка не обязательна: скачайте архив rclone
для своей ОС с rclone.org, распакуйте во временную папку и запустите оттуда команду, которую напечатал мастер:
```
rclone authorize "drive" "<строка из мастера>"
```
Откроется браузер: войдите в аккаунт Google, разрешите доступ. В терминале появится блок `{"access_token":"...","token_type":...}`.
Скопируйте его (это секрет) и вставьте в мастер на ВМ в ответ на `config_token>`. Затем: Configure as Shared Drive? `n` ->
`y` (сохранить) -> `q`. После этого временную папку с rclone на компьютере владельца удалите; токен остаётся только в
`~/.config/rclone/rclone.conf` на ВМ (права `600`). Бэкапы и скрипты на компьютер владельца при этом не попадают.

Запасной вариант без rclone у владельца: SSH-туннель `ssh -L 53682:127.0.0.1:53682 user@vm-re.simbirsoft`, на ВМ в мастере
ответить на вопрос про браузер `y`, открыть напечатанную ссылку `http://127.0.0.1:53682/auth?...` в браузере владельца.

**[ВМ]** проверка:
```bash
chmod 600 ~/.config/rclone/rclone.conf
rclone lsd gdrive:                                  # ожидаемо: без ошибок (при scope drive.file список может быть пустым)
rclone mkdir gdrive:re_web_presale_backups && echo ok
```
Включение (в `/opt/re_web_presale/ops.env`, значения не печатать): `OFFSITE_RCLONE_REMOTE=gdrive:re_web_presale_backups`
и выбранное шифрование (раздел 8). Затем вручную `bash scripts/offsite_sync.sh` с `ops.env` в окружении: строки `restore_checked`,
`offsite_uploaded`, `done`; после этого `sudo systemctl enable --now re-offsite.timer`.

## 8. РЕШЕНИЕ ВЛАДЕЛЬЦА / БЕЗОПАСНОСТИ: шифрование выгрузки

**Предупреждение.** Дампы `cv_db` и `app_db` содержат **персональные данные сотрудников** (файлы CV целиком, ФИО, контакты).
Выгрузка в личный Google Диск - передача этих данных третьей стороне. Рекомендация: **не выгружать без шифрования**.
Поэтому скрипт без явного решения не работает: при `OFFSITE_ENCRYPT=none` (или пусто) он отказывается выгружать, пока не задано
`OFFSITE_ALLOW_PLAINTEXT=1` (то есть решение принято осознанно владельцем). Владельцу/безопасности стоит также подтвердить,
что хранение таких данных в личном облаке допустимо политиками компании и 152-ФЗ; корпоративное хранилище, если появится,
предпочтительнее.

| Вариант | `OFFSITE_ENCRYPT` | Где ключ | Плюсы / минусы |
|---|---|---|---|
| rclone crypt | `crypt` | пароль и соль в `rclone.conf` **на ВМ** | просто; защищает от доступа к Диску, но не от взлома ВМ (там и токен, и ключ). Пароль и соль сохранить в менеджере паролей: без них копии не прочитать |
| gpg (рекомендуется) | `gpg` | на ВМ только **публичный** ключ; закрытый хранится отдельно от Диска и от ВМ | взлом ВМ или Диска не раскрывает выгруженное; для восстановления закрытый ключ обязателен (потеря ключа = потеря копий). Файлы `.dump.gpg` |
| без шифрования | `none` + `OFFSITE_ALLOW_PLAINTEXT=1` | - | только по осознанному решению владельца |

Настройка **crypt** (**[ВМ]**): `rclone config` -> `n` -> имя `gdrive-crypt` -> тип `crypt` -> remote `gdrive:re_web_presale_backups` ->
шифровать имена файлов `standard`, имена каталогов `true` -> пароли: сгенерировать (`g`) и сохранить в менеджер паролей
(`rclone config show gdrive-crypt` печатает их обфусцированными, это не защита). В `ops.env`:
`OFFSITE_RCLONE_REMOTE=gdrive-crypt:` и `OFFSITE_ENCRYPT=crypt` (скрипт проверяет, что remote действительно типа crypt).

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

Восстановление из облака: скачать файл (`rclone copy gdrive:re_web_presale_backups/monthly/<файл> .` на ВМ или через веб-интерфейс
Диска), расшифровать (для `crypt` расшифровка происходит при `rclone copy gdrive-crypt:...` сама), проверить
`restore_check.sh <файл>`, затем действовать по разделу 4. Пока схема не проверена на тестовом дампе, копию рабочей не считать:
после настройки сделайте одно пробное восстановление из облака.

## 9. Почта: алерты и месячный отчёт

Получатель по умолчанию - `ALERT_EMAIL_TO=konstantin.shakurov@simbirsoft.com` (в `ops.env.example`). Отправка - `scripts/send_mail.py`
(стандартная библиотека Python, `smtplib`). Настройки в `ops.env`: `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_SECURITY`
(`starttls` / `ssl` / `none`), `SMTP_USER`, `SMTP_FROM`, `SMTP_VERIFY_TLS=0` для внутреннего сервера с самоподписанным сертификатом.
**Пароль SMTP** - только в `SMTP_PASSWORD` внутри `ops.env` (права 600) либо в отдельном файле (права 600), путь в
`SMTP_PASSWORD_FILE`; в скрипты, журнал и репозиторий он не попадает.

**Хост SMTP владелец пока не назвал.** Пока `SMTP_HOST` пуст, письма не отправляются: алерты и отчёт видны в журнале
(`journalctl -u 're-*'`) и в файлах `reports/`, скрипты не падают. Вебхук (`ALERT_WEBHOOK_URL`) и `ALERT_CMD`/`REPORT_CMD`
остаются как альтернативы и работают вместе с почтой, если заданы.

Что приходит письмом: алерты (`/health` недоступен, контейнер упал или `unhealthy`, бэкап или выгрузка в облако устарели, сбой
дампа, restore-check, выгрузки) - по одному письму на инцидент; месячный отчёт доступности (1-го числа в 09:10, Markdown во
вложении и в теле). Проверка отправки после настройки: `python3 -I scripts/send_mail.py --subject "тест" <<< "проверка"` с
`ops.env` в окружении.

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
- Заполнен 2026 год и **предварительно** 2027. Список составлен без доступа к официальному тексту: **перед использованием
  сверить** с постановлением Правительства РФ (производственный календарь), особенно 2027 (там только праздники по ст. 112 ТК,
  возможные переносы около 1-8 января, 23 февраля и 31 декабря не учтены). Файл правит человек, раз в год.
- Если в файле нет дат за год отчёта или файл не читается, отчёт всё равно строится и пишет предупреждение, что праздники не
  исключены.
- **Работающие субботы (перенесённые рабочие дни) не учитываются**: окно отчёта - только пн-пт. Сокращённые предпраздничные
  дни тоже не учитываются (окно считается целиком).

## 12. Что проверено локально, а что нет

Проверено (Docker 28.3.3, одноразовый контейнер `postgres:16` с двумя тестовыми БД, не стенд проекта; `rclone` и SMTP-сервер
заменены локальными заглушками: `rclone` на машине разработки нет, в сеть и на ВМ не ходили):
- дамп двух БД и ежемесячная копия, `.sha256`, ротация, restore-check (исправный, усечённый, с неверной суммой), сбой `pg_dump`,
  удаление временных БД;
- `offsite_sync.sh`: выключен по умолчанию (код 0); отказ без шифрования и без `OFFSITE_ALLOW_PLAINTEXT=1`; режим `none` с
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

**Не проверено:** реальный `rclone` и Google Диск (токен, drive.file, `rclone check` по хэшам Диска, настоящий crypt, лимиты,
корзина); реальный SMTP (STARTTLS/SSL, авторизация, доставка в `simbirsoft.com`, спам-фильтры); установка и срабатывание
systemd-юнитов и таймеров (в т.ч. `re-offsite.*`; `systemd-analyze verify` не прогонялся); права пользователя `user` на docker и
каталоги; `AUTOHEAL=1` на стенде ВМ; реальный объём дампов; боевая БД с ролями `app_rw`/`cv_rw`; самоподписанный TLS ВМ;
правильность `holidays_ru.txt` (сверить с официальным календарём); хранение 5 лет по факту.
Известная мелочь: при ротации осиротевший `.sha256` без дампа может остаться до своей даты (на работу не влияет).

## 13. Допущения

- Файлы CV лежат в `cv_db` (bytea, `cv.employee_file_content`, ADR-009), поэтому дамп БД покрывает и файлы.
- Внешнее хранилище - Google Диск владельца через rclone (решение владельца), необязательное; пока оно не включено, копий вне ВМ
  нет, и потеря диска ВМ = потеря бэкапов.
- Рабочие часы A7 = местное время ВМ (`Europe/Samara`).
- Алерты и отчёт без `SMTP_HOST` видны только в `journalctl -u re-*` и в файлах.
- Запрет выгрузки без шифрования по умолчанию (`OFFSITE_ALLOW_PLAINTEXT`) - моя защитная настройка из-за персональных данных;
  владелец может снять её осознанным решением.
