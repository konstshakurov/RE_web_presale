# Задача для OpenCode: установить бэкапы и мониторинг на ВМ (KON-49)

Условие: корпоративный VPN, доступ на ВМ `user@vm-re.simbirsoft` по ключу `~/.ssh/id_ed25519_vm_re` с парольной фразой — её вводит Константин (ssh-agent или попроси его ввести; в чат и файлы фраза не попадает). Одно активное подключение к ВМ за раз. Приложение на ВМ уже развёрнуто (`infra/deploy_report.md`), каталог `/opt/re_web_presale/app` — рабочая копия репозитория.

## Зачем

Скрипты бэкапа БД на 5 лет, мониторинга, отчёта доступности, письма и (необязательно) выгрузка в корпоративное облако `https://cloud.simbirsoft1.com/` подготовлены и проверены локально (`infra/backup_monitoring.md` — читать целиком, особенно разделы 2, 7, 8, 9). Нужно **поставить их на ВМ и проверить вживую**. Решения владельца уже приняты и внесены в инструкцию; не пересказывай и не меняй их.

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
Оставь по умолчанию из шаблона: `AUTOHEAL=1`, `SMTP_HOST=relay01.simbirsoft`, `SMTP_PORT=25`, `SMTP_SECURITY=none`, пустые `SMTP_USER`/`SMTP_PASSWORD`, `SMTP_FROM=re-web-presale@simbirsoft`, `ALERT_EMAIL_TO=konstantin.shakurov@simbirsoft.com`, `OFFSITE_REMOTE_DIR=RE_web_presale`; остальное пусто. Хост SMTP у владельца не спрашивай: он уже решён и стоит в шаблоне. Только если файл `ops.env` уже существовал без этих значений, сообщи об этом, не перезаписывая файл.

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
Размер дампов (`du -h`) запиши в отчёт — по нему владелец оценит объём облака.

### Шаг 4. Почта (relay компании, порт 25, без авторизации и TLS)

Настройки уже в `ops.env` из шаблона (шаг 2); пароль не нужен. Проверка доставки тестовым письмом:
```bash
set -a; . /opt/re_web_presale/ops.env; set +a
python3 -I /opt/re_web_presale/app/scripts/send_mail.py --subject "re_web_presale: тест почты" <<< "Проверка отправки из установки бэкапов (KON-49)"; echo rc=$?
```
Ожидаемо: строка `mail_sent`, `rc=0`. Попроси Константина подтвердить, что письмо пришло на `konstantin.shakurov@simbirsoft.com`.
При `mail_failed` (relay отказал: недоступен, не принимает отправителя `re-web-presale@simbirsoft`, нужна авторизация и т.п.) - **СТОП**: верни текст ошибки (он без пароля) и остановись на этом пункте; настройки (хост, порт, отправитель, `SMTP_SECURITY`) ты не подбираешь, решает владелец. Остальные шаги можно продолжать без почты.

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

### Шаг 6. Внешняя копия в корпоративное облако cloud.simbirsoft1.com (необязательно, только по решению владельца)

> **Стоп-точка 1 (секрет владельца: логин и пароль приложения облака).** Логин и **пароль приложения** (app password) для `cloud.simbirsoft1.com` создаёт и вводит **владелец сам** в мастере `rclone config` на ВМ (в твоём терминале ввод идёт от него). Ты их не придумываешь, не просишь прислать в чат, Linear, отчёт или командную строку и не читаешь. Нет логина и пароля приложения - остановись и жди.

> **Стоп-точка 2 (решение владельца: шифрование) — РЕШЕНО 29.10.2026: шифрование НЕ включать, хранить в контуре без шифрования (`OFFSITE_ENCRYPT=none`, `OFFSITE_ALLOW_PLAINTEXT=1`; это и есть прямое «да» владельца). Вопрос не задавай; спроси только, включать ли выгрузку к моменту стоп-точки 3.** Ниже исходная формулировка для справки: Спроси Константина письменно в чате: (а) включать ли выгрузку; (б) вариант: хранить **в контуре без шифрования** (нужен `OFFSITE_ALLOW_PLAINTEXT=1`) либо шифровать `gpg` (публичный ключ на ВМ, закрытый - у владельца отдельно) или `crypt`. Дампы содержат персональные данные сотрудников (CV). Не выбирай за него и не ставь `OFFSITE_ALLOW_PLAINTEXT=1` без его прямого «да». Подробно - `infra/backup_monitoring.md`, раздел 8. Доступ к папке `RE_web_presale` в облаке ограничивает владелец.

> **Стоп-точка 3 (приёмка: тестовая выгрузка и проверка папки в облаке).** После ручного прогона `offsite_sync.sh` попроси Константина открыть облако и подтвердить, что папка `RE_web_presale` с подкаталогами `daily` и `monthly` и файлами на месте. Только после его «да» включай `re-offsite.timer`.

Если выгрузка не нужна - пропусти шаг, зафиксируй в отчёте «внешняя копия не включена по решению владельца».

Если нужна, поэтапно, **ты готовишь, Константин вводит секреты**:
1. `sudo apt install -y rclone gnupg`; `rclone version | head -1`.
2. **Определение типа облака** (запросы без логина, только чтение):
   ```bash
   curl -s https://cloud.simbirsoft1.com/status.php; echo
   curl -si -X PROPFIND https://cloud.simbirsoft1.com/remote.php/dav | head -n 12
   ```
   Облако предположительно Nextcloud/ownCloud с WebDAV (не проверено). Ожидаемо: `status.php` отдаёт JSON с `"installed":true` (и обычно `productname`), `/remote.php/dav` отвечает `401` с `WWW-Authenticate`. По `productname` выбери `vendor` (`nextcloud` или `owncloud`). Если ответ другой (404, HTML без JSON, другая платформа) или тип неясен - **СТОП и вопрос владельцу**, remote не создавай. Результат (без секретов) - в отчёт.
3. Настройка remote `simbircloud` (стоп-точка 1): `rclone config` -> `n` -> имя `simbircloud` -> тип `webdav` -> url `https://cloud.simbirsoft1.com/remote.php/dav/files/<логин>/` -> vendor по п. 2 -> user `<логин>` -> pass: ввод своего пароля (`y`) делает **владелец**; мастер сохраняет его обфусцированным (`rclone obscure`) -> `y` -> `q`. Пароль нигде, кроме `rclone.conf` на ВМ, не фигурирует: не в `ops.env`, не в репозитории, не в аргументах команд. `rclone config show` не запускать.
4. Проверка: `chmod 600 ~/.config/rclone/rclone.conf`; `rclone lsd simbircloud:` - без ошибок (вывод - только факт успеха/ошибки, без перечисления чужих папок в отчёте).
5. Шифрование по решению владельца (стоп-точка 2, раздел 8 инструкции):
   - без шифрования: только после его явного «да» - `OFFSITE_ENCRYPT=none` и `OFFSITE_ALLOW_PLAINTEXT=1`;
   - `crypt`: создать remote `simbircloud-crypt` над `simbircloud:RE_web_presale_enc` (пароли генерирует rclone, `g`); **сообщи Константину сохранить пароль и соль в менеджер паролей** (сам их не читай и не выводи);
   - `gpg`: публичный ключ `re_backup_public.asc` Константин кладёт на ВМ сам (или называет путь); ты делаешь `gpg --import` и `gpg --list-keys <ID>`. Закрытый ключ на ВМ не создавать и не принимать.
6. Впиши в `ops.env` (без печати значений) `OFFSITE_RCLONE_REMOTE=simbircloud:` (для crypt - `simbircloud-crypt:`), `OFFSITE_ENCRYPT` и, для gpg, `OFFSITE_GPG_RECIPIENT`. `OFFSITE_REMOTE_DIR` оставь `RE_web_presale`.
7. **Тестовая выгрузка** (ручной прогон): `set -a; . /opt/re_web_presale/ops.env; set +a; bash /opt/re_web_presale/app/scripts/offsite_sync.sh; echo rc=$?`. Ожидаемо: `restore_checked`, `offsite_uploaded` (новых файлов 4), `done`, `rc=0`; `rclone lsf -R simbircloud:RE_web_presale` показывает `daily/` и `monthly/` (для gpg - файлы `*.dump.gpg`; для crypt - через crypt-remote). Повторный запуск: `offsite_uptodate`. Если `rclone check` сообщает, что общих хэшей нет - верни текст ошибки владельцу, проверку не отключай.
8. Стоп-точка 3: подтверждение владельца, что папка `RE_web_presale` в облаке с `daily` и `monthly` видна и в ней файлы.
9. Только после этого: `sudo systemctl enable --now re-offsite.timer`, `systemctl list-timers 're-offsite*' --no-pager`.
10. Пробное восстановление из облака (по разделу 8 инструкции) - с участием Константина, если выбран gpg (закрытый ключ у него).

### Шаг 7. Итоговая проверка

`systemctl list-timers 're-*' --no-pager`; `systemctl --failed --no-pager | grep re- || echo "упавших юнитов нет"`; `docker ps --format '{{.Names}} {{.Status}}'` (все `healthy`); `ls -l /var/backups/re_web_presale/last_success`; `https://vm-re.simbirsoft/health` → `{"status":"ok"}`. Права: `ls -l /opt/re_web_presale/ops.env` (`600`), `ls -ld /var/backups/re_web_presale` (`700`).

## Чего НЕ делать

- Не ставить скрипты, таймеры, дампы, ключи на локальные компьютеры (OpenCode и Константина). Не копировать дампы с ВМ никуда.
- Не печатать и не сохранять: содержимое `ops.env`, `rclone.conf`, `.env`, логин и пароль приложения облака, пароль SMTP (если появится), пароли и соль crypt, закрытый gpg-ключ, пароли БД, ключ SSH. В отчёте — только «задано / пусто» и маски.
- Не придумывать и не подбирать: логин и пароль приложения облака, хост/порт/отправителя SMTP сверх шаблона, ID gpg-ключа, решение о шифровании, тип облака (если не подтверждён проверкой). Нет значения — остановиться и спросить.
- Не включать `re-offsite.timer`, пока владелец не принял решение по шифрованию (стоп-точка 2) и не подтвердил результат тестовой выгрузки в облаке (стоп-точка 3). Не ставить `OFFSITE_ALLOW_PLAINTEXT=1` без прямого «да» владельца.
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
