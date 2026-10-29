# Бэкапы БД на 5 лет и мониторинг (KON-49)

Статус: **подготовлено в репозитории и проверено локально; на ВМ не выложено.** Команды ниже выполняет
владелец (или OpenCode по постановке) на `vm-re.simbirsoft`. Секреты в файлах не хранятся.
Команды помечены **[ВМ]**.

## 1. Что устанавливается

| Что | Файл | Расписание (местное время ВМ, `Europe/Samara`) |
|---|---|---|
| Дамп `app_db` и `cv_db` + ротация | `scripts/backup_db.sh`, `infra/systemd/re-backup.{service,timer}` | ежедневно 02:30 |
| Проверка восстановления во временную БД | `scripts/restore_check.sh`, `re-restore-check.*` | воскресенье 04:00 |
| Проверка `/health`, контейнеров, свежести бэкапа | `scripts/monitor_health.sh`, `re-health.*` | каждую минуту |
| Отчёт доступности пн-пт 9:00-18:00 за прошлый месяц | `scripts/availability_report.{sh,py}`, `re-availability-report.*` | 1-го числа в 09:10 |
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
nano /opt/re_web_presale/ops.env      # BACKUP_DIR, ALERT_WEBHOOK_URL / ALERT_CMD, REPORT_CMD

# 3. проверить, что нет CRLF в скриптах (после доставки с Windows)
grep -lc $'\r' /opt/re_web_presale/app/scripts/*.sh /opt/re_web_presale/app/scripts/*.py /opt/re_web_presale/app/infra/systemd/* || echo "CRLF нет"
# при наличии: sed -i 's/\r$//' <файл>

# 4. ручной прогон до включения таймеров
set -a; . /opt/re_web_presale/ops.env; set +a
bash /opt/re_web_presale/app/scripts/backup_db.sh
bash /opt/re_web_presale/app/scripts/restore_check.sh
bash /opt/re_web_presale/app/scripts/monitor_health.sh
tail -n1 /var/lib/re_web_presale/monitor/health-$(date +%Y%m).jsonl
# ожидаемо: dump_ok для двух БД, restore_ok для двух БД, в журнале "ok":true

# 5. включить таймеры
sudo cp /opt/re_web_presale/app/infra/systemd/re-*.service /opt/re_web_presale/app/infra/systemd/re-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now re-backup.timer re-restore-check.timer re-health.timer re-availability-report.timer
systemctl list-timers 're-*' --no-pager
```

Если на ВМ предпочитают cron вместо systemd: те же скрипты, `crontab -e` под `user`
(переменные из `ops.env` экспортируйте строкой `. /opt/re_web_presale/ops.env;` перед командой):
```
30 2 * * *   set -a; . /opt/re_web_presale/ops.env; bash /opt/re_web_presale/app/scripts/backup_db.sh
0 4 * * 0    set -a; . /opt/re_web_presale/ops.env; bash /opt/re_web_presale/app/scripts/restore_check.sh
* * * * *    set -a; . /opt/re_web_presale/ops.env; bash /opt/re_web_presale/app/scripts/monitor_health.sh
10 9 1 * *   set -a; . /opt/re_web_presale/ops.env; bash /opt/re_web_presale/app/scripts/availability_report.sh
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
sudo systemctl disable --now re-backup.timer re-restore-check.timer re-health.timer re-availability-report.timer
sudo rm /etc/systemd/system/re-*.service /etc/systemd/system/re-*.timer && sudo systemctl daemon-reload
# каталоги /var/backups/re_web_presale и /var/lib/re_web_presale/monitor с данными НЕ удалять без решения владельца
```

## 6. Мониторинг: что считается и как проверить

- `/health` раз в минуту: успех = HTTP 200 и `"status":"ok"`; запись в `health-YYYYMM.jsonl`. Алерт после
  `HEALTH_FAIL_THRESHOLD` (3) неудач подряд, один раз на инцидент (повторно - после восстановления).
- Те же прогоны проверяют, что контейнеры проекта `Up` и не `unhealthy`, и что последний успешный бэкап не старше
  36 ч. `AUTOHEAL=1` перезапускает `unhealthy`-контейнер (Docker сам этого не делает; по умолчанию выключено).
- Отчёт: `availability_report.sh [YYYY-MM]` - доступность = успешные минуты / все минуты окна 9:00-18:00 пн-пт;
  минуты без записи считаются недоступными; праздничные дни не исключаются (допущение A7). Результат -
  `$MONITOR_DIR/reports/availability-YYYY-MM.md` и `REPORT_CMD`, если задан.
- Падение контейнера: restart policy поднимает контейнер, завершившийся сам (проверено, раздел 7).
  **Важно:** `docker kill`/`docker stop` Docker считает ручной остановкой и не перезапускает. Для приёмочной проверки
  используйте падение процесса, а не `docker kill`. Не гонять эту проверку на боевой ВМ без согласования.

## 7. Что проверено локально, а что нет

Проверено (Docker 28.3.3, одноразовый контейнер `postgres:16` с двумя тестовыми БД, не стенд проекта):
дамп двух БД и ежемесячная копия; `.sha256`; ротация (старый ежедневный и старый ежемесячный файлы удалены);
restore-check на исправном дампе (таблицы и строки сошлись), на усечённом дампе и с неверной суммой (код 1, алерт);
сбой `pg_dump` (код 1, алерт через `ALERT_CMD`); временные БД после проверок удалены; `monitor_health.sh` против
работающего стенда (`/health` -> ok, запись в журнал, алерт после 3 неудач, один раз, запись о восстановлении, алерт о
старом бэкапе); отчёт по синтетическому журналу сентября (расчёт и интервалы сверены вручную); `restart: unless-stopped`
поднимает контейнер, завершившийся с ошибкой; `bash -n` всех скриптов.

**Не проверено** (нужна ВМ или внешние адреса): установка и работа systemd-юнитов и таймеров (синтаксис юнитов не
прогонялся через `systemd-analyze`); права пользователя `user` на docker и каталоги; доставка алертов и отчёта по
вебхуку/почте (получатель не определён); `AUTOHEAL=1`; реальный объём дампов на данных ВМ; запуск на боевой БД с ролями
`app_rw`/`cv_rw` и миграциями; `pg_restore` боевого дампа с правами ролей; поведение на самоподписанном TLS ВМ
(`curl -k` учтён); срабатывание таймера по расписанию (в т.ч. часовой пояс); хранение 5 лет по факту.
Известная мелочь: при ротации осиротевший `.sha256` без дампа может остаться до своей даты (на работу не влияет).

## 8. Допущения

- Файлы CV лежат в `cv_db` (bytea, `cv.employee_file_content`, ADR-009), поэтому дамп БД покрывает и файлы.
- Хранилища вне ВМ нет (`infra/vm.md`): локальные бэкапы + ручная выгрузка владельца; потеря диска = потеря бэкапов.
- Рабочие часы A7 = местное время ВМ (`Europe/Samara`).
- Алерты и отчёт без доставки видны только в `journalctl -u re-*` и в файлах.
