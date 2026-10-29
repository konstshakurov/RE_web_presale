# Задача для OpenCode: установить бэкапы и мониторинг на ВМ (KON-49)

Условие: корпоративный VPN, доступ на ВМ `user@vm-re.simbirsoft` по ключу `~/.ssh/id_ed25519_vm_re` с парольной фразой — её вводит Константин (ssh-agent или попроси его ввести; в чат и файлы фраза не попадает). Одно активное подключение к ВМ за раз. Приложение на ВМ уже развёрнуто (`infra/deploy_report.md`), каталог `/opt/re_web_presale/app` — рабочая копия репозитория.

## Зачем

Скрипты бэкапа БД на 5 лет, мониторинга, отчёта доступности, письма и (необязательно) выгрузка в Google Диск подготовлены и проверены локально (`infra/backup_monitoring.md` — читать целиком, особенно разделы 2, 7, 8, 9). Нужно **поставить их на ВМ и проверить вживую**. Решения владельца уже приняты и внесены в инструкцию; не пересказывай и не меняй их.

**Главное правило: всё устанавливается и работает только на ВМ.** На компьютер, где запущен OpenCode, и на компьютер Константина ничего из скриптов, таймеров, дампов и ключей не ставится и не копируется (кроме отчёта в репозитории).

## Что сделать

Строго по порядку; после каждого шага — проверка «Ожидаемо», результат в отчёт.

### Шаг 0. Предусловия (только чтение)

На ВМ: `hostname`, `docker ps --format '{{.Names}} {{.Status}}'` (все `healthy`), `df -h /`, `ls -ld /var/backups/re_web_presale /opt/re_web_presale/app`, `command -v flock curl python3 bash`, `timedatectl | grep 'Time zone'`.
Ожидаемо: контейнеры `healthy`, каталог бэкапов существует (`700`, владелец `user`), пояс `Europe/Samara`. Если нет — остановись и сообщи.

### Шаг 1. Доставка кода на ВМ

Скрипты и юниты должны попасть в `/opt/re_web_presale/app/scripts/`, `.../infra/systemd/`, `.../infra/ops/` теми же способом и состава, что в `infra/deploy.md` (архив/`scp`). Доставь **только** эти пути: `scripts/*.sh`, `scripts/*.py`, `infra/systemd/`, `infra/ops/`. Остальное на ВМ не трогай.
Проверки на ВМ:
```bash
grep -lc $'\r' /opt/re_web_presale/app/scripts/*.sh /opt/re_web_presale/app/scripts/*.py /opt/re_web_presale/app/infra/systemd/* /opt/re_web_presale/app/infra/ops/* || echo "CRLF нет"
# при CRLF: sed -i 's/\r$//' <файл>, затем повторить grep
for f in /opt/re_web_presale/app/scripts/*.sh; do bash -n "$f" || echo "BAD $f"; done
python3 -I -m py_compile /opt/re_web_presale/app/scripts/send_mail.py /opt/re_web_presale/app/scripts/availability_report.py; rm -rf /opt/re_web_presale/app/scripts/__pycache__
```
Ожидаемо: «CRLF нет», ни одного `BAD`, `py_compile` без ошибок.

### Шаг 2. Каталоги и ops.env

```bash
sudo mkdir -p /var/lib/re_web_presale/monitor && sudo chown user:user /var/lib/re_web_presale/monitor && chmod 700 /var/lib/re_web_presale/monitor
[ -e /opt/re_web_presale/ops.env ] || cp /opt/re_web_presale/app/infra/systemd/ops.env.example /opt/re_web_presale/ops.env
chmod 600 /opt/re_web_presale/ops.env; ls -l /opt/re_web_presale/ops.env
```
Если `ops.env` уже есть — не перезаписывай. Содержимое файла и значения переменных не печатай; проверяй только «задано/пусто» по именам (`grep -E '^SMTP_HOST=.+' ... | cut -d= -f1`, без значения).
Оставь по умолчанию: `AUTOHEAL=1`, `ALERT_EMAIL_TO=konstantin.shakurov@simbirsoft.com`, остальное пусто.

> **Стоп-точка 1 (решение/секрет владельца: SMTP).** Хост SMTP, порт, логин, пароль и `SMTP_FROM` знает только Константин. Если `SMTP_HOST` пуст — **остановись на этом пункте и спроси**, не придумывай и не подбирай; продолжай остальные шаги без почты (скрипты пишут только в журнал и не падают). Пароль SMTP вписывает Константин сам в `ops.env` на ВМ (или передаёт путь файла `SMTP_PASSWORD_FILE` с правами 600); в чат, отчёт, командную строку он не попадает. После того как он вписал значения — проверка отправки (шаг 4).

### Шаг 3. Ручной прогон до включения таймеров

```bash
set -a; . /opt/re_web_presale/ops.env; set +a
bash /opt/re_web_presale/app/scripts/backup_db.sh;        echo rc=$?
bash /opt/re_web_presale/app/scripts/restore_check.sh;    echo rc=$?
bash /opt/re_web_presale/app/scripts/monitor_health.sh;   echo rc=$?
bash /opt/re_web_presale/app/scripts/offsite_sync.sh;     echo rc=$?
tail -n1 /var/lib/re_web_presale/monitor/health-$(date +%Y%m).jsonl
ls -l /var/backups/re_web_presale/daily /var/backups/re_web_presale/monthly
```
Ожидаемо: `dump_ok` для `app_db` и `cv_db`, `restore_ok` для обеих (число таблиц и строк), `rc=0` везде, `offsite_disabled` (облако ещё не включено), в журнале `"ok":true`, файлы дампов и `.sha256` с правами `600`. Если дамп или restore-check не прошёл — остановись и верни вывод ошибки (без значений секретов).
Размер дампов (`du -h`) запиши в отчёт — по нему владелец оценит объём Диска.

### Шаг 4. Почта (после стоп-точки 1)

Только если Константин вписал SMTP:
```bash
set -a; . /opt/re_web_presale/ops.env; set +a
python3 -I /opt/re_web_presale/app/scripts/send_mail.py --subject "re_web_presale: тест почты" <<< "Проверка отправки из установки бэкапов (KON-49)"; echo rc=$?
```
Ожидаемо: строка `mail_sent`, `rc=0`. Попроси Константина подтвердить, что письмо пришло на `konstantin.shakurov@simbirsoft.com`. При `mail_failed` — верни текст ошибки (он без пароля) и остановись; настройки (хост, порт, безопасность, `SMTP_VERIFY_TLS`) подбирает владелец, ты их не угадываешь.

### Шаг 5. Включить таймеры (без выгрузки в облако)

```bash
sudo cp /opt/re_web_presale/app/infra/systemd/re-*.service /opt/re_web_presale/app/infra/systemd/re-*.timer /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/re-*.service /etc/systemd/system/re-*.timer 2>&1 | head -20
sudo systemctl daemon-reload
sudo systemctl enable --now re-backup.timer re-restore-check.timer re-health.timer re-availability-report.timer
systemctl list-timers 're-*' --no-pager
```
Ожидаемо: у четырёх таймеров есть `NEXT`; `systemd-analyze verify` без ошибок (предупреждения про отсутствующий `/opt/re_web_presale/ops.env` допустимы только если файла нет — он должен быть). Таймер `re-offsite.timer` **не включать** до шага 6 и решения владельца.
Подожди 2–3 минуты, затем: `journalctl -u re-health -n 5 --no-pager` (запуски раз в минуту, без ошибок) и `tail -n3 /var/lib/re_web_presale/monitor/health-$(date +%Y%m).jsonl` (`"ok":true`).

### Шаг 6. Внешняя копия в Google Диск (необязательно, только по решению владельца)

> **Стоп-точка 2 (решение владельца/безопасности).** Спроси Константина письменно в чате: (а) включать ли выгрузку в Google Диск; (б) **шифрование**: `crypt` (rclone crypt), `gpg` (публичный ключ на ВМ, закрытый — у владельца отдельно) или без шифрования. Дампы содержат **персональные данные сотрудников (CV)**; без явного «да, без шифрования» (`OFFSITE_ALLOW_PLAINTEXT=1`) скрипт отказывается выгружать. Не выбирай за него. Подробно — `infra/backup_monitoring.md`, раздел 8.

Если выгрузка не нужна — пропусти шаг, зафиксируй в отчёте «внешняя копия не включена по решению владельца».

Если нужна, поэтапно, **ты готовишь, Константин вводит секреты**:
1. `sudo apt install -y rclone gnupg`; `rclone version | head -1`.
2. Токен Google Диска (**стоп-точка 3, секрет владельца**): запусти на ВМ `rclone config` по разделу 7.2 инструкции (имя remote `gdrive`, тип `drive`, scope `drive.file` (пункт 3), «Use web browser» — `n`). Мастер напечатает команду `rclone authorize "drive" "…"`. Передай её Константину **как есть** (это не секрет). Он выполнит её у себя (временный rclone, без установки), получит токен и **сам вставит его в мастер на ВМ** (в твоём терминале ввод идёт от него). Токен тебе не показывают и ты его не читаешь, не копируешь и не пишешь в отчёт. Если токена нет — остановись и жди.
3. Проверка: `chmod 600 ~/.config/rclone/rclone.conf`; `rclone lsd gdrive:` — без ошибок; `rclone mkdir gdrive:re_web_presale_backups && echo ok`.
4. Шифрование по выбору владельца (раздел 8 инструкции):
   - `crypt`: создать remote `gdrive-crypt` (пароли генерирует rclone, `g`); **сообщи Константину сохранить пароль и соль в менеджер паролей** (сам их не читай и не выводи; `rclone config show gdrive-crypt` не запускать);
   - `gpg`: публичный ключ `re_backup_public.asc` Константин кладёт на ВМ сам (или называет путь); ты делаешь `gpg --import` и `gpg --list-keys <ID>`. Закрытый ключ на ВМ не создавать и не принимать.
5. Впиши в `ops.env` (без печати значений) `OFFSITE_RCLONE_REMOTE`, `OFFSITE_ENCRYPT` и, для gpg, `OFFSITE_GPG_RECIPIENT`.
6. Ручной прогон: `set -a; . /opt/re_web_presale/ops.env; set +a; bash /opt/re_web_presale/app/scripts/offsite_sync.sh; echo rc=$?`. Ожидаемо: `restore_checked`, `offsite_uploaded` (новых файлов 4), `done`, `rc=0`; `rclone lsf -R <remote>` показывает `daily/` и `monthly/` (для gpg — файлы `*.dump.gpg`; для crypt видно расшифрованные имена). Повторный запуск: `offsite_uptodate`.
7. Только после успешного прогона: `sudo systemctl enable --now re-offsite.timer`, `systemctl list-timers 're-offsite*' --no-pager`.
8. Пробное восстановление из облака (по разделу 8 инструкции) — с участием Константина, если выбран gpg (закрытый ключ у него).

### Шаг 7. Итоговая проверка

`systemctl list-timers 're-*' --no-pager`; `systemctl --failed --no-pager | grep re- || echo "упавших юнитов нет"`; `docker ps --format '{{.Names}} {{.Status}}'` (все `healthy`); `ls -l /var/backups/re_web_presale/last_success`; `https://vm-re.simbirsoft/health` → `{"status":"ok"}`. Права: `ls -l /opt/re_web_presale/ops.env` (`600`), `ls -ld /var/backups/re_web_presale` (`700`).

## Чего НЕ делать

- Не ставить скрипты, таймеры, дампы, ключи на локальные компьютеры (OpenCode и Константина). Не копировать дампы с ВМ никуда.
- Не печатать и не сохранять: содержимое `ops.env`, `rclone.conf`, `.env`, пароль SMTP, токен Google, пароли и соль crypt, закрытый gpg-ключ, пароли БД, ключ SSH. В отчёте — только «задано / пусто» и маски.
- Не придумывать и не подбирать: хост/порт/логин/пароль SMTP, remote и токен Google, ID gpg-ключа, решение о шифровании. Нет значения — остановиться и спросить.
- Не включать `re-offsite.timer`, пока владелец не принял решение по шифрованию (стоп-точка 2) и ручной прогон не прошёл. Не ставить `OFFSITE_ALLOW_PLAINTEXT=1` без прямого «да» владельца.
- Не менять код продукта, `docker-compose*.yml`, `.env`, `.env.example`, `src/`, `backend/`, `frontend/`, `db/`. Не перезапускать и не пересобирать контейнеры приложения, не выполнять `docker compose up/down`, `docker system prune`, удалять тома и данные. Не `docker kill`/`stop` боевых контейнеров (проверка AUTOHEAL на боевом стенде без согласования запрещена).
- Не менять скрипты и юниты на ВМ «по месту». Нашёл ошибку — в отчёт, правка идёт через репозиторий (devops-агент).
- Не перезагружать ВМ, не менять ufw, sshd, пользователей, sudoers, часовой пояс. Не удалять каталоги `/var/backups/re_web_presale`, `/var/lib/re_web_presale`.
- Не коммитить в репозиторий (коммит делает Константин).
- Не ходить в Jira, LLM, не входить в приложение.

## Ограничения безопасности

Одно активное подключение к ВМ. Перед каждым изменением системы (юниты, каталоги, `ops.env`) убедись, что откат известен (`infra/backup_monitoring.md`, раздел 5: `systemctl disable --now re-*.timer`, удаление юнитов). Все выполненные команды и ключевые части вывода — в отчёт, секреты маскировать (`<скрыто>`).

## Область записи

На ВМ — только то, что перечислено выше (`/opt/re_web_presale/app/{scripts,infra}`, `/opt/re_web_presale/ops.env`, `/etc/systemd/system/re-*`, `/var/lib/re_web_presale/monitor`, `/var/backups/re_web_presale` — только через скрипты, `~/.config/rclone/`, связка gpg пользователя `user` — только публичный ключ). В репозитории — только `infra/backup_install_report.md`.

## Что вернуть

Заполненный `infra/backup_install_report.md` по шаблону из этого файла (шаблон лежит там же), и короткий комментарий в Linear к KON-49: что поставлено, какие таймеры активны, какие стоп-точки ждут Константина, чего не удалось. Статус KON-49 не менять.
