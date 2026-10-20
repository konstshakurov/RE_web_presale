# Отчёт о подготовке ВМ `vm-re.simbirsoft` (KON-49, шаг 0)

**Дата:** 2026-10-20
**Хост:** `vm-re.simbirsoft` (`10.73.11.110`), хостнейм `vm-re`
**ОС:** Ubuntu 24.04.5 LTS (noble), ядро `6.8.0-147-generic`
**Пользователь:** `user` (в группах `adm cdrom sudo dip plugdev lxd docker`)
**Доступ:** только по SSH-ключу (`PasswordAuthentication no`), ключ `~/.ssh/id_ed25519_vm_re`
**Источник инструкции:** `infra/vm_setup.md` (разделы 1–9 + чек-лист раздела 9)
**Статус:** ✅ **ВМ готова к деплою.** Приложение не разворачивалось (как и требовалось).

> В этом отчёте секреты (пароли, ключи шифрования, содержимое `.env`, приватный ключ) **не приводятся**. Значения, сгенерированные на ВМ, обозначены как `<скрыто>`. Имена хостов, пользователей и неконфиденциальные значения остаются открытыми.

---

## 0. Итоговая сводка

| Параметр | Значение |
|---|---|
| Проверка SSH-доступа по ключу | ✅ работает (`KEY_LOGIN=user@vm-re`) |
| Пароль по SSH | ✅ **отклоняется** (`Permission denied (publickey)`) |
| Docker Engine | ✅ **29.9.1** (build dfc520d) |
| Docker Compose | ✅ **v5.6.0** (`docker compose`) |
| containerd | ✅ v2.4.2 |
| docker.service | ✅ `enabled` + `active` |
| Ротация логов Docker | ✅ `json-file`, `max-size=10m`, `max-file=5`, `live-restore=true` |
| ufw | ✅ active; разрешены только `22/tcp` и `443/tcp`; порт 80 закрыт |
| Автообновления | ✅ включены (`Update-Package-Lists 1`, `Unattended-Upgrade 1`), автоперезагрузка выключена |
| Часовой пояс / NTP | ✅ `Europe/Samara (+04)`, `System clock synchronized: yes`, `NTP service: active` |
| Диск `/` | ✅ `130G`, занято `7.1G` (6%), доступно `118G` (не ~30 ГБ — ок) |
| Память | ✅ `7.8Gi` RAM, swap `3.8Gi` |
| Каталоги | ✅ `/opt/re_web_presale` (750), `.../tls` (700), `/var/backups/re_web_presale` (700) |
| `.env` | ✅ `/opt/re_web_presale/.env`, права `600 user:user`, секреты заданы |
| Контроль свободного места | ✅ скрипт + cron каждые 15 минут |
| Перезагрузка после обновления ядра | ✅ выполнена, `reboot-required` снят |

**Требуется действие владельца:** см. раздел «Что владелец должен сделать вручную» в конце.

---

## 1. Доступ и первичная настройка SSH

### 1.1. Подключение и первичная диагностика — ✅ Ожидаемо

- ОС: `Ubuntu 24.04.2 LTS` (noble), хостнейм `vm-re`.
- `user` в группах: `adm cdrom sudo dip plugdev lxd`.
- `passwd -S user` → `user P 2026-10-20 0 99999 7 -1` — пароль **не просрочен**, срок истечения отсутствует.
- **Принудительной смены пароля при первом входе ВМ не потребовала**, поэтому остановка не выполнялась (условие «если ВМ требует смену пароля — остановиться» не сработало).

### 1.2. Смена пароля пользователя — ⚠️ **Намеренно не выполнено (отклонение от `vm_setup.md`)**

- В `infra/vm_setup.md` §1.2 предусмотрена смена пароля `user`.
- **Отклонение:** задача KON-49 (шаг 0) **прямо запрещает** менять пароль `user`. Приоритет — запрет из задачи. Пароль не изменялся средствами агента.
- См. «Что владелец должен сделать вручную»: пароль `user` ранее был передан в открытом виде в чате, поэтому **его следует сменить силами владельца** и сохранить в менеджере паролей.

### 1.3. Отпечатки ключей хоста — ✅ зафиксированы

Отпечатки получены реальным подключением `ssh` (`StrictHostKeyChecking=accept-new`); `ssh-keyscan` неприменим (локальный OpenSSH 9.5p2 не поддерживает KEX `sntrup761x25519-sha512` ВМ).

| Тип ключа | SHA256 | MD5 |
|---|---|---|
| ED25519 | `SHA256:hP7W0bJCh6rQ77fsVg9k96e4TnpXSPVPRPH/o2GZFK0` | `f7:be:17:fa:c4:79:06:73:e6:a2:94:68:73:ce:50:d5` |
| ECDSA (nistp256) | `SHA256:OBmjqAtm0nIQaOo6p8Z2Rfkinh7oEaz3OJWaUAGOcdQ` | `6a:1a:4b:36:28:dd:6e:26:15:6c:74:65:6b:7b:7b:bd` |
| RSA (3072) | `SHA256:VqsBtedfOVUZCMWoQn+KUI6U1NWR0426JHEGnrf/fl8` | `1e:76:40:2c:1a:7b:2d:f3:24:2b:56:77:71:f9:67:01` |

Баннер сервера: `SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13.20`.

### 1.4. Генерация ключа — ✅ Ожидаемо

- Создан ключ **ed25519**: `C:\Users\user\.ssh\id_ed25519_vm_re` (+ `.pub`), комментарий `re_web_presale vm-re`.
- Отпечаток публичного ключа: `SHA256:vo+ZtD3cWe9QaUhQ7NJqeFToktsYj1wwHRKYuh9s0XM`.
- **Отклонение:** ключ сгенерирован **без парольной фразы** (неинтерактивная генерация на стороне агента). Рекомендация владельцу — задать парольную фразу и сохранить бэкап ключа в менеджере паролей.
- Приватный ключ в репозиторий, отчёт и чат **не попадал**.

### 1.5. Публичный ключ в `authorized_keys` — ✅ Ожидаемо

```
drwx------  /home/user/.ssh              (700)
-rw-------  /home/user/.ssh/authorized_keys   (600, 1 строка)
```

### 1.6. Проверка входа по ключу — ✅ Ожидаемо

Новое подключение `ssh -i id_ed25519_vm_re ... user@vm-re.simbirsoft` → `KEY_LOGIN_OK`, exit 0.

### 1.8. Ужесточение конфигурации sshd — ✅ Ожидаемо

Файл `/etc/ssh/sshd_config.d/00-hardening.conf`:

```
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
```

- `sshd -t` → `CONFIG_OK` (конфиг валиден).
- `sshd -T` (эффективно, под root):

```
permitrootlogin no
pubkeyauthentication yes
passwordauthentication no
kbdinteractiveauthentication no
```

- `reload ssh` → `RELOADED`.
- Проверка **из нового подключения**: вход по ключу работает; вход по паролю → `Permission denied (publickey)`.
- **Сбой при верификации не возникал**, откат не потребовался.

---

## 2. Обновление системы, время, NTP — ✅ Ожидаемо

- `apt update` — без ошибок; `apt upgrade -y` — **89 пакетов обновлено**, 1 «kept back» (`fwupd`, некритично), ошибок `E:` не было.
- `initramfs` пересобран под новое ядро `6.8.0-147-generic` → появился флаг `/var/run/reboot-required`.
- Часовой пояс выставлен: `Europe/Samara (+04, +0400)`.
- `timedatectl`:

```
Time zone: Europe/Samara (+04, +0400)
System clock synchronized: yes
NTP service: active
```

Сервер NTP: `ntp.ubuntu.com`.

### Перезагрузка ВМ — ✅ выполнена (по согласованию с владельцем)

- Первая попытка перезагрузки через `paramiko` не сработала (sudo не получил пароль в фоне) — исправлено повторной корректной командой.
- Подтверждение: `boot_id` сменился (`514502b8…` → `7a27be08…`), `uptime` = 0 мин.
- Сразу после перезагрузки был **временный** сбой сети (см. §4, `curl: (28) SSL connection timeout`) — сам по себе прошёл.
- После перезагрузки: `no_reboot_needed` (флаг снят), ядро `6.8.0-147-generic`, часовой пояс и синхронизация сохранены.

---

## 3. Базовая безопасность: ufw и автообновления — ✅ Ожидаемо

### 3.1. ufw

Установлен (`0.36.2-6`, уже присутствовал). Политики и правила:

```
Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), deny (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW IN    Anywhere
443/tcp                    ALLOW IN    Anywhere
22/tcp (v6)                ALLOW IN    Anywhere (v6)
443/tcp (v6)               ALLOW IN    Anywhere (v6)
```

- Порт **80 закрыт**.
- Ограничений по подсети нет (как и решено ранее).
- **fail2ban не устанавливался** (по задаче).
- Порядок применения соблюдён: правила 22/443 добавлены **до** `ufw enable`. SSH-доступ после включения проверен из нового подключения (`SSH_OK_AFTER_UFW`).

### 3.2. Автообновления (unattended-upgrades)

Установлен. Включение выполнено неинтерактивно через `debconf-set-selections` + `dpkg-reconfigure`. `/etc/apt/apt.conf.d/20auto-upgrades`:

```
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
```

- Автоперезагрузка **выключена** (все `Automatic-Reboot` в `50unattended-upgrades` закомментированы).
- Dry-run (`unattended-upgrade --dry-run --debug`) прошёл **без `Traceback`**.
- Таймеры активны:

```
apt-daily.timer         → следующая 2026-10-21 04:31 +04
apt-daily-upgrade.timer → следующая 2026-10-21 06:42 +04
```

---

## 4. Docker Engine и Compose v2 — ✅ Ожидаемо (после повторной попытки)

### Отклонение/сбой

- Первая установка упала: `curl: (28) SSL connection timeout` при скачивании GPG-ключа `download.docker.com`. Сбой совпал с моментом сразу после перезагрузки ВМ.
- Диагностика показала, что доступ **есть**: `download.docker.com/gpg` → 200, `.../InRelease` → 200, `registry-1.docker.io/v2/` → 401 (норма), `auth.docker.io/token` → 200, TCP 443 → OK.
- Вывод: **временный сетевой сбой**. Повторная установка прошла успешно.

### Результат

- GPG-ключ Docker скачан, отпечаток подтверждён: `9DC8 5822 9FC7 DD38 854A E2D8 8D81 803C 0EBF CD88` (Docker Release (CE deb) `<docker@docker.com>`).
- Репозиторий: `deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable`.
- `apt update` — без ошибок.
- Установлены пакеты:

| Пакет | Версия |
|---|---|
| `containerd.io` | `2.4.2-2~ubuntu.24.04~noble` |
| `docker-ce` | `5:29.9.1-1~ubuntu.24.04~noble` |
| `docker-ce-cli` | `5:29.9.1-1~ubuntu.24.04~noble` |
| `docker-buildx-plugin` | `0.38.0-1~ubuntu.24.04~noble` |
| `docker-compose-plugin` | `5.6.0-1~ubuntu.24.04~noble` |
| `docker-ce-rootless-extras` | `5:29.9.1-...` |

- `docker --version` → `Docker version 29.9.1, build dfc520d`
- `docker compose version` → `Docker Compose version v5.6.0`
- Сервис: `systemctl is-enabled docker` → **`enabled`**, `is-active` → **`active`**.
- `user` добавлен в группу `docker`: `id -nG` → `user adm cdrom sudo dip plugdev lxd docker`.
- **`docker run --rm hello-world` без sudo** — ✅ `Hello from Docker!` (после ретраев; первый запуск упал по `TLS handshake timeout` — снова временный сетевой сбой).

---

## 5. Логи Docker и лимиты ресурсов

### 5.1. Ротация логов — ✅ Ожидаемо

`/etc/docker/daemon.json` (до этого файла не было):

```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "5"
  },
  "live-restore": true
}
```

- JSON-валидация: `JSON_OK`.
- `systemctl restart docker` → `RESTARTED`.
- `docker info` → `LoggingDriver=json-file`, `LiveRestoreEnabled=true`.
- Проверка на контейнере: `docker inspect logtest` → `{json-file map[max-file:5 max-size:10m]}`.

### 5.2. Лимиты ресурсов — ⏭️ **Не выполнено (по задаче)**

Настройка лимитов CPU/RAM для контейнеров (`mem_limit`, `cpus` в compose) — **отдельная задача**, в шаг 0 не входит. В `docker-compose*.yml` правки не вносились.

---

## 6. Каталоги приложения и бэкапов — ✅ Ожидаемо

```
drwxr-x---  /opt/re_web_presale           (750, user:user)
drwx------  /opt/re_web_presale/tls       (700, user:user)
drwx------  /var/backups/re_web_presale   (700, user:user)
```

---

## 7. Диск, память, контроль свободного места — ✅ Ожидаемо

### `df -h /`

```
Filesystem      Size  Used Avail Use% Mounted on
/dev/sda2       130G  7.1G  118G   6% /
```

**Диск ~130 ГБ, а не ~30 ГБ** — LVM растянут корректно. Остановка/возврат вывода не требовались.

### `lsblk`

```
NAME   MAJ:MIN RM  SIZE RO TYPE MOUNTPOINTS
sda      8:0    0  132G  0 disk
├─sda1   8:1    0    1M  0 part
└─sda2   8:2    0  132G  0 part /
sr0     11:0    1 1024M  1 rom
```

### `free -h`

```
               total        used        free      shared  buff/cache   available
Mem:           7.8Gi       547Mi       6.4Gi       1.0Mi       1.1Gi       7.2Gi
Swap:          3.8Gi          0B       3.8Gi
```

### Контроль свободного места

- Скрипт `/usr/local/bin/check_disk_space.sh` (755, root): при занятости `/` ≥ порога (по умолчанию 80%) пишет в системный журнал `DISK_LOW`, иначе — `disk ok`.
- Cron `/etc/cron.d/check_disk_space`:

```
*/15 * * * * root /usr/local/bin/check_disk_space.sh 80
```

- Прогон: `disk ok: / used 6%`.
- Тест порога (1%): `DISK_LOW: / used 6% (threshold 1%)` — срабатывает корректно.
- `docker system df`: 2 образа (hello-world, alpine), ~13 МБ.

---

## 8. Файл `.env` на ВМ — ✅ Ожидаемо

- Шаблон `.env.example` скопирован на ВМ (117 строк, через SFTP), из него создан `.env`:

```
-rw-------  /opt/re_web_presale/.env   (600 user:user)
```

- Блок Confluence (строки 1–25) **удалён**. Проверка: `grep -c CONFLUENCE .env` → **0**.
- **Секреты сгенерированы на ВМ** и записаны **прямо в файл** (значения в экран/журнал/отчёт не выводились):
  - `POSTGRES_PASSWORD`, `APP_RW_PASSWORD`, `CV_RW_PASSWORD` → `<скрыто>` (по 32 байта urlsafe);
  - `APP_ENC_KEY`, `JIRA_TOKEN_ENCRYPTION_KEY` → `<скрыто>` (по 32 случайных байта base64);
  - `APP_DB_URL` / `CV_DB_URL` собраны из соответствующих паролей (значения в вывод не попадали).
- Неконфиденциальные значения заданы: `APP_ENC_KEY_ID=k1`, `LDAP_URL=ldap://freeipa01.simbirsoft:389/`, `LDAP_BIND_TEMPLATE=uid={login},cn=users,cn=accounts,dc=simbirsoft,dc=dev`, `LDAP_BASE_DN=cn=users,cn=accounts,dc=simbirsoft,dc=dev`, `LDAP_USE_TLS=false`, `JIRA_BASE_URL=https://jira.simbirsoft.com`, `SESSION_COOKIE_SECURE=true`.
- **Пусто** (оставлено владельцу): `ADMIN_LOGINS`, `LDAP_USER_FILTER`, `LDAP_BIND_DN`, `LDAP_BIND_PASSWORD`, `LDAP_TIMEOUT_SEC`, `SESSION_TTL_HOURS`, `TEST_LDAP_DOMAIN`, `TEST_LDAP_ADMIN_PASSWORD`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_TASK_TTL_DAYS`, `CV_MAX_FILE_MB`, все `SYNC_*`, все `JIRA_*_PROJECT/ISSUETYPE`, `JIRA_UTC_OFFSET_HOURS`, `WORKER_HEARTBEAT_PATH`, `PROXY_PORT`.
- Проверка «задано/пусто» выполнена скриптом, печатающим **только имена** и признак; значения не раскрывались:

```
POSTGRES_PASSWORD            задано
APP_RW_PASSWORD              задано
CV_RW_PASSWORD               задано
APP_DB_URL                   задано
CV_DB_URL                    задано
APP_ENC_KEY                  задано
APP_ENC_KEY_ID               задано
ADMIN_LOGINS                 (пусто)
LDAP_URL                     задано
LDAP_BIND_TEMPLATE           задано
LDAP_BASE_DN                 задано
LDAP_USE_TLS                 задано
SESSION_COOKIE_SECURE        задано
JIRA_BASE_URL                задано
JIRA_TOKEN_ENCRYPTION_KEY    задано
LLM_API_KEY                  (пусто)
...
```

- `.env` **не** попадает в git (есть в `.gitignore`). Файл `.env.example` в репозитории не изменялся.

---

## 9. Чек-лист «ВМ готова к деплою» (пункты 1–20)

| № | Проверка | Результат | Комментарий |
|---|---|---|---|
| 1 | Вход по SSH-ключу работает | ✅ | `KEY_LOGIN=user@vm-re` |
| 2 | Вход по паролю отключён | ✅ | `passwordauthentication no` |
| 3 | Пароль `user` изменён владельцем | ⚠️ не проверено | **действие владельца** (§1.2; запрещено менять агентом) |
| 4 | Система обновлена | ✅ | upgradable: 1 (`fwupd`, некритично) |
| 5 | Часовой пояс `Europe/Samara` | ✅ | `+04, +0400` |
| 6 | ufw активен, только 22 и 443 | ✅ | порт 80 закрыт |
| 7 | Автообновления включены, без автоперезагрузки | ✅ | `20auto-upgrades` = 1/1 |
| 8 | NTP синхронизирован | ✅ | `synchronized: yes`, `NTP active` |
| 9 | Docker Engine установлен | ✅ | `29.9.1` |
| 10 | Compose v2 доступен | ✅ | `docker compose version` → `v5.6.0` |
| 11 | `docker` работает без sudo | ✅ | `hello-world` без sudo |
| 12 | docker.service `enabled` | ✅ | `enabled` + `active` |
| 13 | Ротация логов Docker | ✅ | `json-file` 10m×5, `live-restore` |
| 14 | Каталоги и права созданы | ✅ | 750 / 700 / 700 |
| 15 | Диск `/` достаточного размера | ✅ | `130G`, занято 6% |
| 16 | Контроль свободного места настроен | ✅ | cron + скрипт, журнал пишется |
| 17 | `.env` на месте, права 600 | ✅ | `600 user:user`, без Confluence |
| 18 | Обязательные переменные заполнены | ✅ | все обязательные «задано» |
| 19 | Секреты сгенерированы и записаны | ✅ | на ВМ, значения `<скрыто>` |
| 20 | Исходящий доступ: Jira | ✅ | `https://jira.simbirsoft.com` → HTTP 200 |
| 20b | Доступ к LDAP-порту | ✅ | `freeipa01.simbirsoft:389` → `LDAP_PORT_OK` |

> Примечание: нумерация чек-листа в отчёте соответствует логике `vm_setup.md`; п. 3 — единственный, который агент не выполняет по запрету задачи.

---

## 10. Что сломалось и почему

| Событие | Причина | Действие |
|---|---|---|
| `curl (28) SSL connection timeout` при скачивании GPG Docker | Временный сетевой сбой сразу после перезагрузки ВМ | Повтор успешен; доступ подтверждён диагностикой |
| `docker run hello-world`: `TLS handshake timeout` | Временная нестабильность до Docker Hub (Cloudflare) | Повтор (ретраи) — `Hello from Docker!` |
| Первая перезагрузка через `paramiko` не сработала | `sudo -S` в фоне не получил пароль | Повтор корректной командой, `boot_id` сменился |
| `fwupd` «kept back» при обновлении | Пакет не обновился в рамках обычного upgrade | Некритично, к деплою не относится |
| `apparmor.postinst: 148: [: Illegal number: yes` | Косметическая ошибка скрипта пакета | Без последствий |
| Первая проверка `.env` «задано/пусто» показала всё «задано» | Ошибка парсинга в проверочной awk-команде | Проверка переписана на Python — результат корректен |

---

## 11. Отклонения от инструкции `vm_setup.md`

| № | Отклонение | Причина |
|---|---|---|
| 1 | Смена пароля `user` (§1.2) не выполнена | Прямой запрет в задаче KON-49; отнесено к владельцу |
| 2 | SSH-ключ сгенерирован **без парольной фразы** | Неинтерактивная автоматизация; рекомендация владельцу — защитить ключ |
| 3 | Docker устанавливался повторно после сетевого сбоя | Первая попытка упала по `curl (28)` |
| 4 | «задано/пусто» проверялось Python-скриптом, а не awk | Корректность разбора строк `.env` |
| 5 | Лимиты ресурсов контейнеров (§5.2) не настраивались | Отдельная задача, вне шага 0 |

---

## 12. Что владелец должен сделать вручную

1. **Сменить пароль пользователя `user`** (в т.ч. потому что он ранее передавался в открытом виде в чате) и **сохранить его в менеджере паролей**. Агент пароль не менял.
2. **Задать парольную фразу для `~/.ssh/id_ed25519_vm_re`** и **сохранить бэкап приватного ключа** в менеджере паролей. Публичный ключ: `SHA256:vo+ZtD3cWe9QaUhQ7NJqeFToktsYj1wwHRKYuh9s0XM`.
3. **Сохранить бэкап `/opt/re_web_presale/.env`** в менеджере паролей: файл содержит сгенерированные пароли БД и ключи шифрования (`<скрыто>`), восстановить их иначе нельзя.
4. **Заполнить оставшиеся пустыми значения** в `/opt/re_web_presale/.env` перед деплоем:
   - `ADMIN_LOGINS` (администраторы приложения);
   - `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_TASK_TTL_DAYS` (доступ к LLM);
   - `LDAP_USER_FILTER`, `LDAP_BIND_DN`, `LDAP_BIND_PASSWORD` (если нужен bind-аккаунт);
   - `TEST_LDAP_DOMAIN`, `TEST_LDAP_ADMIN_PASSWORD` (для интеграционных тестов);
   - параметры `SYNC_*`, `JIRA_*` (проекты/типы), `CV_MAX_FILE_MB`, `SESSION_TTL_HOURS`, `LDAP_TIMEOUT_SEC`, `WORKER_HEARTBEAT_PATH`, `PROXY_PORT` — по фактической конфигурации.
5. **Вопрос в Support** (не блокирует остальное): подтвердить стабильность исходящего доступа к Docker Hub и к `download.docker.com` — наблюдались периодические TLS-таймауты. При повторе проблем — попросить зеркало/прокси.
6. **Проверить стабильность исходящего доступа к Jira и LDAP** (пп. 19–20 на момент проверки прошли: HTTP 200 и `LDAP_PORT_OK`) — при деградации это вопрос к Support.
7. **При следующем обновлении ядра** — согласовать перезагрузку (в этот раз выполнена по вашему решению).

---

## 13. Приложение А. Фактические артефакты/команды

Ключевые конфиги и факты (для воспроизведения проверок):

```bash
# доступ
ssh -i ~/.ssh/id_ed25519_vm_re user@vm-re.simbirsoft

# timezone/NTP
timedatectl

# ufw
sudo ufw status verbose

# docker
docker --version && docker compose version
systemctl is-enabled docker && systemctl is-active docker
docker info --format '{{.LoggingDriver}} live-restore={{.LiveRestoreEnabled}}'
docker run --rm hello-world

# sshd hardening
cat /etc/ssh/sshd_config.d/00-hardening.conf
sudo sshd -T | grep -E '^(passwordauthentication|permitrootlogin)'

# каталоги и .env
ls -ld /opt/re_web_presale /opt/re_web_presale/tls /var/backups/re_web_presale
stat -c '%a %U %G' /opt/re_web_presale/.env

# диск/память
df -h / ; lsblk ; free -h
```

**Запись в репозиторий:** только `infra/vm_setup_report.md` (этот файл). Прочие файлы репозитория и `docker-compose*.yml`/`.env.example` не изменялись. Все временные скрипты автоматизации лежали вне репозитория (в `%TEMP%\opencode\`).
