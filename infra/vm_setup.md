# Первичная подготовка ВМ (KON-49, шаг 0)

Инструкция для владельца. Все команды вводит владелец сам; DevOps-агент на ВМ не заходит.
Данные ВМ — в `infra/vm.md`. Хост: `vm-re.simbirsoft`, пользователь: `user`, Ubuntu 24.04 LTS, 8 vCPU, 8 ГБ RAM, 132 ГБ SSD.

## Как пользоваться

- Каждая команда — в своём блоке. Копируйте по одной, дожидайтесь конца, сверяйте с «Ожидаемо».
- Блоки с пометкой **[PowerShell]** вводятся на вашем компьютере (Windows). Остальные — на ВМ, после входа по SSH.
- Слова в угловых скобках (`<...>`) — плейсхолдеры, подставьте своё. Реальные пароли, ключи и токены **не вставляйте** в чат, Linear, репозиторий и файлы проекта.
- Если вывод не совпал с ожидаемым — остановитесь и пришлите текст вывода (без паролей) в чат. Не пропускайте шаг.
- **Золотое правило:** пока не проверили вход по ключу из **второго** окна, первое окно с открытой сессией на ВМ не закрывайте.

Допущения: ВМ доступна из вашей сети (VPN/офис); `sudo` для `user` есть (пароль `user` = пароль входа); нужны порты 22 и 443 (по `infra/support_vm_request.md`).

---

## 1. Первый вход, смена пароля, переход на SSH-ключ

### Карта раздела (прочитайте сначала)

| Шаг | Что | Обязательно? |
|---|---|---|
| 1.1 | Первый вход по паролю | да |
| 1.2 | Смена пароля | да |
| 1.3 | Проверка `sudo` | да |
| 1.4 | Создать SSH-ключ на вашем компьютере | рекомендовано (вход по ключу — рекомендованный вариант) |
| 1.5 | Положить публичный ключ на ВМ | рекомендовано (после 1.4) |
| 1.6 | Проверить вход по ключу из нового окна | обязательно, если сделали 1.4–1.5 |
| 1.7 | Короткое имя `ssh vm-re` | по желанию |
| 1.8 | Отключить вход по паролю | только после успешного 1.6 |

Если вы пока остаётесь на пароле, шаги 1.4–1.8 можно отложить: 1.1–1.3 и разделы 2 и далее работают и так. Второй ключ не нужен.

**Что делать при ошибке на любом шаге:** остановитесь, дальше не идите. Скопируйте текст ошибки (без паролей) и пришлите в чат. Пока не пройдён шаг 1.6, ничего не отключайте и не закрывайте окно с открытой сессией на ВМ.

**Как открыть PowerShell в Windows:** клавиша Win, набрать `PowerShell`, Enter. Копировать в окно: правая кнопка мыши вставляет скопированный текст. Пароль при вводе не отображается (ни точек, ни звёздочек) — это нормально, вводите и нажимайте Enter. «Окно ВМ» — окно PowerShell, где вы вошли по `ssh` (приглашение `user@...:~$`); «окно компьютера» — любое окно PowerShell с приглашением `PS C:\...>`.

### 1.1. Первый вход [PowerShell]

```powershell
ssh user@vm-re.simbirsoft
```

Что делает: подключается к ВМ. Пароль вводится вслепую (символы не отображаются) — это нормально.
Ожидаемо: при первом подключении вопрос `Are you sure you want to continue connecting (yes/no/[fingerprint])?` — введите `yes`. Затем запрос пароля и приглашение вида `user@...:~$`.
Если не подключается (`Could not resolve hostname`): имя не распознаётся — проверьте, что вы в сети компании или на VPN; если не помогло, спросите у Support IP-адрес ВМ и временно используйте его вместо `vm-re.simbirsoft` (адрес `vm-re.simbirsoft` остаётся основным, решение владельца). Если `Connection timed out`: проверьте VPN/сеть.
После успешного входа вы остаётесь в этом окне — оно «окно ВМ» (выйти: `exit`).

### 1.2. Смена пароля (на ВМ)

```bash
passwd
```

Что делает: меняет пароль пользователя `user`. Спросит текущий пароль, затем новый дважды.
Ожидаемо: `passwd: password updated successfully`.
Риск: новый пароль сохраните в корпоративный менеджер паролей сразу. Пароль, выданный Support, мог быть временным и пройти принудительную смену при входе — это тоже нормально.

### 1.3. Проверка прав sudo (на ВМ)

```bash
sudo whoami
```

Ожидаемо: запрос пароля, затем `root`. Если `user is not in the sudoers file` — остановитесь, обратитесь в Support.

### 1.4. Создать ключ на вашем компьютере [PowerShell]

Откройте **новое** окно PowerShell (на ВМ не нужно); окно с сессией на ВМ не закрывайте. Все команды 1.4–1.7 вводятся в этом новом окне.

```powershell
ssh -V
```

Ожидаемо: строка `OpenSSH_for_Windows_...`. Если команда не найдена — Параметры Windows → Приложения → Дополнительные компоненты → «Клиент OpenSSH».

```powershell
ssh-keygen -t ed25519 -C "re_web_presale vm-re" -f "$env:USERPROFILE\.ssh\id_ed25519_vm_re"
```

Что делает: создаёт пару ключей. Приватный — `id_ed25519_vm_re`, публичный — `id_ed25519_vm_re.pub`.
Спросит passphrase (пароль на ключ, вводится дважды, не отображается): рекомендуется задать (иначе любой, кто получит файл, попадёт на ВМ); можно нажать Enter и оставить пустым. Если спросит «Overwrite (y/n)?» — ключ с таким именем уже есть, ответьте `n` и сообщите в чат.
Ожидаемо: `Your identification has been saved in ...id_ed25519_vm_re` и «randomart».
Если папки `.ssh` нет и команда ругается — выполните `mkdir $env:USERPROFILE\.ssh` и повторите.
Риски:
- **Приватный ключ (файл без `.pub`) никому не отправляйте, не кладите в репозиторий и чат.** Передаётся только `.pub`.
- Сделайте копию приватного ключа в защищённое место (менеджер паролей/зашифрованное хранилище) — потеря ключа = потеря доступа после отключения паролей.

### 1.5. Скопировать публичный ключ на ВМ [PowerShell]

```powershell
type "$env:USERPROFILE\.ssh\id_ed25519_vm_re.pub" | ssh user@vm-re.simbirsoft "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

Что делает: дописывает публичный ключ в `~/.ssh/authorized_keys` на ВМ (в Windows нет `ssh-copy-id`, поэтому так). Это одна длинная команда — вставляйте целиком. Спросит пароль ВМ (новый, из шага 1.2). Если выполнить дважды, ключ запишется дважды — это безвредно.
Ожидаемо: команда завершается без вывода.

### 1.6. Проверить вход по ключу — из НОВОГО окна [PowerShell]

```powershell
ssh -i "$env:USERPROFILE\.ssh\id_ed25519_vm_re" -o PreferredAuthentications=publickey -o PasswordAuthentication=no user@vm-re.simbirsoft
```

Что делает: пытается войти строго по ключу, пароль запрещён.
Ожидаемо: вход состоялся (запросит только passphrase ключа, если задавали), приглашение `user@...:~$`.
Если `Permission denied (publickey)`: ключ не принят. **На этом шаге останавливайтесь, пароль не отключайте.** Проверьте шаг 1.5 (в первом окне на ВМ: `ls -la ~/.ssh` — права `drwx------` на папку и `-rw-------` на `authorized_keys`).

### 1.7. Удобство: короткое имя [PowerShell] (по желанию)

```powershell
notepad "$env:USERPROFILE\.ssh\config"
```

Вставьте и сохраните (если файла нет — согласитесь на создание; следите, чтобы Блокнот не добавил `.txt`):

```
Host vm-re
    HostName vm-re.simbirsoft
    User user
    IdentityFile ~/.ssh/id_ed25519_vm_re
```

Проверка: `ssh vm-re` входит на ВМ.

### 1.8. Отключить вход по паролю — ТОЛЬКО после успешного 1.6

В первом окне (уже открытая сессия на ВМ):

```bash
sudo tee /etc/ssh/sshd_config.d/00-hardening.conf > /dev/null <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
```

Что делает: создаёт файл настроек. Имя `00-` важно: в Ubuntu первое найденное значение побеждает, а облачный файл `50-cloud-init.conf` может включать пароль.
Ожидаемо: без вывода.

```bash
sudo sshd -t && echo CONFIG_OK
```

Ожидаемо: `CONFIG_OK` (без ошибок). Иначе не продолжайте: `sudo rm /etc/ssh/sshd_config.d/00-hardening.conf`.

```bash
sudo sshd -T | grep -E "^(passwordauthentication|kbdinteractiveauthentication|permitrootlogin)"
```

Ожидаемо: `passwordauthentication no`, `kbdinteractiveauthentication no`, `permitrootlogin no`.

```bash
sudo systemctl reload ssh
```

Ожидаемо: без вывода. Уже открытые сессии остаются.

**Проверка из нового окна [PowerShell]:**

```powershell
ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no user@vm-re.simbirsoft
```

Ожидаемо: `Permission denied (publickey)` — парольный вход закрыт. И `ssh vm-re` (или команда из 1.6) по-прежнему входит.

### Как не потерять доступ

- Не закрывайте первое окно, пока и вход по ключу, и отказ по паролю не проверены.
- Если сломали настройку: в открытой сессии `sudo rm /etc/ssh/sshd_config.d/00-hardening.conf && sudo systemctl reload ssh` вернёт вход по паролю.
- Если сессий не осталось и ключ не работает: обращайтесь в Support (консоль ВМ/сброс доступа). Поэтому храните резервную копию приватного ключа и пароль `user`.
- Второй ключ не нужен (решение владельца). Если доступ понадобится другому человеку или машине позже, повторите 1.4–1.5 с неё.

---

## 2. Обновление системы, часовой пояс, NTP

```bash
sudo apt update && sudo apt upgrade -y
```

Что делает: обновляет пакеты. Может идти несколько минут. Если появится розовое окно про перезапуск служб — Enter (значения по умолчанию).
Ожидаемо: завершается без `E:` ошибок.

```bash
[ -f /var/run/reboot-required ] && echo "NEED_REBOOT" || echo "no reboot needed"
```

Если `NEED_REBOOT`: `sudo reboot`, подождите 1–2 минуты и войдите снова (`ssh vm-re`). Перезагрузка делается один раз сейчас, пока на ВМ ничего не работает.

```bash
sudo timedatectl set-timezone <Region/City>
```

Что делает: ставит часовой пояс. Решение владельца (подтверждено 20.10.2026): `Europe/Samara` (UTC+4), то есть команда: `sudo timedatectl set-timezone Europe/Samara`. Отчёты доступности 9:00–18:00 считаются по этому поясу.

```bash
timedatectl
```

Ожидаемо: `Time zone: Europe/Samara (...)`, `System clock synchronized: yes`, `NTP service: active`.
Если `synchronized: no`: подождите минуту и повторите. Если не помогло — в сети может быть свой NTP-сервер, спросите Support; исходящий NTP (UDP 123) мог быть закрыт.

```bash
timedatectl show-timesync --property=ServerName --property=NTPSynchronized
```

Ожидаемо: `NTPSynchronized=yes`.

---

## 3. Базовая безопасность

### 3.1. Файрвол ufw

Что открываем (решение владельца, 20.10.2026): SSH (22/tcp) и HTTPS приложения (443/tcp) без ограничения по подсети — ВМ доступна только во внутренней сети компании. Порт 80 закрыт (приложение работает только по HTTPS, редирект не нужен). Всё остальное входящее закрыто.

Заметка: ограничение по подсетям (правила `allow from <подсеть> to any port ...`) можно добавить позже, если этого потребует ИБ или Support; сейчас не требуется.

Риск: ошибка в правиле SSH отрежет вас от ВМ. Поэтому порядок строго такой: сначала разрешаем SSH, потом включаем.

```bash
sudo apt install -y ufw
```

```bash
sudo ufw default deny incoming && sudo ufw default allow outgoing
```

Ожидаемо: `Default incoming policy changed to 'deny'` и `Default outgoing policy changed to 'allow'`.

```bash
sudo ufw allow 22/tcp
```

Ожидаемо: `Rule added` (и `Rule added (v6)`).

```bash
sudo ufw allow 443/tcp
```

Ожидаемо: `Rule added`. Порт 80 не открываем.

```bash
sudo ufw enable
```

Спросит подтверждение (`Command may disrupt existing ssh connections`) — `y`. Текущая сессия не оборвётся, так как правило SSH уже есть.

```bash
sudo ufw status verbose
```

Ожидаемо: `Status: active`, `Default: deny (incoming), allow (outgoing)`, правила на 22 и 443.

Проверка: откройте новое окно и выполните `ssh vm-re` — вход работает.

**Важное предупреждение про Docker и ufw.** Порты, опубликованные контейнерами (`ports:` в compose), Docker открывает в обход ufw. Поэтому в продовом compose порты публикуются только осознанно (443 у proxy; БД и API наружу не публикуются — как сейчас в `docker-compose.yml`). Не добавляйте в compose `ports:` для `db`/`api`. Это проверим при деплое.

### 3.2. fail2ban — решено: не ставим

Решение владельца (20.10.2026): fail2ban не устанавливаем. Причины: ВМ доступна только из внутренней сети; при отключённом пароле (шаг 1.8) подбор по SSH бессмыслен; риск заблокировать самого себя выше выигрыша. Раздел не выполняется, в чек-листе пункта нет. Если ИБ компании потребует — вернёмся к вопросу отдельно.

### 3.3. Автоматические обновления безопасности

```bash
sudo apt install -y unattended-upgrades
```

```bash
sudo dpkg-reconfigure -plow unattended-upgrades
```

В окне выберите «Yes». Ожидаемо: создан `/etc/apt/apt.conf.d/20auto-upgrades`.

```bash
cat /etc/apt/apt.conf.d/20auto-upgrades
```

Ожидаемо:

```
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
```

```bash
sudo unattended-upgrade --dry-run --debug 2>&1 | tail -n 5
```

Ожидаемо: завершение без `Traceback`/`ERROR`.

Риск и решение: по умолчанию устанавливаются только обновления безопасности, **автоперезагрузка выключена** (оставьте так). Если `/var/run/reboot-required` появился — перезагружайте вручную в нерабочее время (`restart: unless-stopped` в compose поднимет контейнеры сам, проверим при деплое). Обновление пакета `docker-ce` unattended-upgrades из репозитория Docker по умолчанию не ставит; обновлять Docker будем осознанно.

---

## 4. Docker Engine и Compose v2 (официальный репозиторий Docker)

Нужен исходящий доступ к `download.docker.com` и `registry-1.docker.io` (HTTPS). Если шаги ниже не проходят из-за сети — вернитесь с текстом ошибки, возможно, понадобится зеркало/прокси от Support.

```bash
sudo apt install -y ca-certificates curl
```

```bash
sudo install -m 0755 -d /etc/apt/keyrings
```

```bash
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
```

Что делает: скачивает публичный ключ репозитория Docker. Ожидаемо: без вывода.

```bash
sudo chmod a+r /etc/apt/keyrings/docker.asc
```

```bash
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
```

```bash
cat /etc/apt/sources.list.d/docker.list
```

Ожидаемо: одна строка, оканчивающаяся на `noble stable`.

```bash
sudo apt update
```

Ожидаемо: в списке есть `download.docker.com`, без ошибок GPG.

```bash
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Ожидаемо: установка без ошибок.

```bash
sudo systemctl is-enabled docker && sudo systemctl is-active docker
```

Ожидаемо: `enabled` и `active` (автозапуск после перезагрузки ВМ включён).

```bash
sudo docker --version && sudo docker compose version
```

Ожидаемо: `Docker version 2x.x.x` и `Docker Compose version v2.x.x`. Запишите версии в `infra/vm.md` (воспроизводимость).

### 4.1. Пользователь в группе docker

```bash
sudo usermod -aG docker user
```

Риск: членство в группе `docker` равно root-доступу к ВМ. Допустимо, так как на ВМ работает только владелец.

Выйдите (`exit`) и войдите снова (`ssh vm-re`), чтобы группа применилась.

```bash
id -nG
```

Ожидаемо: в списке есть `docker`.

### 4.2. Проверка hello-world

```bash
docker run --rm hello-world
```

Ожидаемо: текст `Hello from Docker! This message shows that your installation appears to be working correctly.`
Если `pull access denied`/`timeout`: нет доступа к Docker Hub — сообщите Support/DevOps (зеркало).

Заметка: у контейнеров по умолчанию нет `latest`-образов приложения; версии образов в проекте зафиксированы (`postgres:16-alpine`, `nginx:1.27-alpine`).

---

## 5. Лимиты ресурсов и ротация логов Docker

### 5.1. Ротация логов (daemon.json)

Зачем: без ротации лог контейнера растёт бесконечно и забивает диск. Приложение пишет JSON-логи (NFR-006), Docker хранит их в файлах — ограничиваем размер.

Сначала проверьте, что файла ещё нет (иначе не затирайте чужие настройки):

```bash
ls -l /etc/docker/daemon.json
```

Ожидаемо: `No such file or directory`. Если файл есть — пришлите его содержимое, объединим вручную.

```bash
sudo tee /etc/docker/daemon.json > /dev/null <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "5"
  },
  "live-restore": true
}
EOF
```

Что делает: на каждый контейнер максимум 5 файлов по 10 МБ (до 50 МБ на контейнер); `live-restore` — контейнеры не останавливаются при перезапуске службы Docker.

```bash
sudo python3 -c "import json; json.load(open('/etc/docker/daemon.json')); print('JSON_OK')"
```

Ожидаемо: `JSON_OK`.

```bash
sudo systemctl restart docker
```

```bash
docker info --format '{{.LoggingDriver}} live-restore={{.LiveRestoreEnabled}}'
```

Ожидаемо: `json-file live-restore=true`.

Проверка на живом контейнере:

```bash
docker run -d --name logtest --rm alpine sleep 30 && docker inspect logtest --format '{{.HostConfig.LogConfig}}'
```

Ожидаемо: `{json-file map[max-file:5 max-size:10m]}`.
Заметка: настройка действует только на **новые** контейнеры.

### 5.2. Лимиты ресурсов контейнеров

Важно понимать: глобальных лимитов памяти/CPU в `daemon.json` нет — они задаются **в `docker-compose.yml`** на каждый сервис. Это часть деплоя (KON-49), на этом шаге на ВМ ничего вводить не нужно. Предлагаемые стартовые лимиты (допущение, проверим на нагрузке; сумма с запасом под систему из 8 ГБ):

| Сервис | Память | CPU |
|---|---|---|
| db (PostgreSQL) | 2 ГБ | 2 |
| api | 1 ГБ | 2 |
| worker | 1 ГБ | 1 |
| web, proxy | по 256 МБ | 0.5 |

Итого около 4.5 ГБ из 8; остаток — системе и кэшу ОС. Ограничения вносим в compose отдельным изменением (область DevOps), не на ВМ вручную.

---

## 6. Каталоги под приложение, данные и бэкапы

Раскладка (допущение):

| Что | Путь | Права |
|---|---|---|
| Код приложения и compose-файлы | `/opt/re_web_presale/` | владелец `user`, 750 |
| Файл с секретами `.env` | `/opt/re_web_presale/.env` | `user`, 600 |
| TLS-сертификат и ключ (когда выдадут) | `/opt/re_web_presale/tls/` | `user`, 700 (ключ 600) |
| Бэкапы (дампы БД, копия файлов CV) | `/var/backups/re_web_presale/` | `user`, 700 |
| Данные PostgreSQL | том Docker `pgdata`, физически `/var/lib/docker/volumes/` | управляет Docker, вручную не трогать |

```bash
sudo mkdir -p /opt/re_web_presale /opt/re_web_presale/tls /var/backups/re_web_presale
```

```bash
sudo chown -R user:user /opt/re_web_presale /var/backups/re_web_presale
```

```bash
chmod 750 /opt/re_web_presale && chmod 700 /opt/re_web_presale/tls /var/backups/re_web_presale
```

```bash
ls -ld /opt/re_web_presale /opt/re_web_presale/tls /var/backups/re_web_presale
```

Ожидаемо:

```
drwxr-x--- ... user user ... /opt/re_web_presale
drwx------ ... user user ... /opt/re_web_presale/tls
drwx------ ... user user ... /var/backups/re_web_presale
```

### Политика бэкапов (решение владельца, 20.10.2026)

Хранилища вне ВМ у Support нет, поэтому:

- **Локальные бэкапы на ВМ** в `/var/backups/re_web_presale/` — для быстрого восстановления: ежедневный дамп БД (обе БД) и копия каталога файлов CV.
- **Хранение:** ежедневные — 30 дней; ежемесячные — до 5 лет (срок из KON-49, NFR-004).
- **Лимит места:** бэкапы занимают не более 20% диска (около 26 ГБ из 132); при приближении к лимиту — алерт, не молчаливое удаление ежемесячных копий.
- **Вынос за пределы ВМ вручную:** раз в неделю владелец скачивает свежий дамп на свой компьютер (команда ниже).
- **Риск, принят владельцем:** потеря ВМ (диска) = потеря всех локальных бэкапов; свежие данные восстановимы только настолько, насколько свежа последняя ручная выгрузка владельца.

Скачать свежий дамп на свой компьютер [PowerShell] (раз в неделю; точное имя файла уточнится в задаче KON-49 — сначала посмотрите список):

```powershell
ssh vm-re "ls -lt /var/backups/re_web_presale/ | head -n 10"
```

```powershell
scp vm-re:/var/backups/re_web_presale/<имя_файла_дампа> "$env:USERPROFILE\Documents\re_web_presale_backups\"
```

Папку `re_web_presale_backups` создайте заранее (`mkdir "$env:USERPROFILE\Documents\re_web_presale_backups"`). Дамп содержит персональные данные сотрудников: храните на зашифрованном диске, не отправляйте в чат и почту. Подробный скрипт бэкапа и проверка восстановления — задача KON-49 (раздел «Следующие шаги»).

---

## 7. Место на диске и его мониторинг

```bash
df -h /
```

Ожидаемо: размер около 130 ГБ. Если `Size` намного меньше (~30 ГБ, как было до расширения) — корневой том LVM не растянут на весь диск; пришлите вывод `lsblk` и `df -h`, подскажем команды расширения (не выполняйте наугад).

```bash
lsblk
```

Ожидаемо: диск ~132 ГБ, раздел с `/` занимает почти весь диск.

```bash
free -h
```

Ожидаемо: `Mem:` около 7.7–8 ГБ.

### Простой контроль свободного места

Скрипт проверяет `/` раз в 15 минут и при заполнении более 80% пишет запись в системный журнал (приоритет `err`). Доставка уведомлений (почта/мессенджер) — задача мониторинга KON-49.

```bash
sudo tee /usr/local/bin/check_disk_space.sh > /dev/null <<'EOF'
#!/bin/sh
# Пишет в журнал предупреждение, если занято больше порога (по умолчанию 80%) на /
THRESHOLD="${1:-80}"
USED=$(df --output=pcent / | tail -n 1 | tr -dc '0-9')
if [ "$USED" -ge "$THRESHOLD" ]; then
  logger -p user.err -t disk-check "DISK_LOW: / used ${USED}% (threshold ${THRESHOLD}%)"
else
  logger -p user.info -t disk-check "disk ok: / used ${USED}%"
fi
EOF
sudo chmod 755 /usr/local/bin/check_disk_space.sh
```

```bash
echo '*/15 * * * * root /usr/local/bin/check_disk_space.sh 80' | sudo tee /etc/cron.d/check_disk_space > /dev/null
```

```bash
sudo /usr/local/bin/check_disk_space.sh && journalctl -t disk-check -n 3 --no-pager
```

Ожидаемо: запись вида `disk-check: disk ok: / used NN%`.

Проверка срабатывания тревоги:

```bash
sudo /usr/local/bin/check_disk_space.sh 1 && journalctl -t disk-check -n 1 --no-pager
```

Ожидаемо: `DISK_LOW: / used NN% (threshold 1%)`.

Регулярно (раз в месяц, и перед деплоем) смотрите, что занимает место Docker:

```bash
docker system df
```

Очистку (`docker system prune`) без согласования не выполняйте: можно удалить нужные образы; тома с данными удаляются только с флагом `--volumes` — **его не используйте никогда** на этой ВМ.

---

## 8. Файл .env на ВМ без передачи секретов через чат

Принцип: секреты рождаются **на самой ВМ** и остаются на ней. Из чата/репозитория берётся только шаблон (без значений). Рабочий `.env` в Git не попадает (проверьте, что `.env` в `.gitignore`).

Где лежит: `/opt/re_web_presale/.env`, права 600, владелец `user` (compose читает его из каталога проекта).

Шаблон `.env.example` в проекте содержит также блок Confluence для платформы аналитика — он **не нужен** приложению; на ВМ берём только блок re_web_presale (после строки «re_web_presale (KON-32)»).

### 8.1. Положить шаблон на ВМ

Код на ВМ ещё не развёрнут (деплой — следующий шаг KON-49), поэтому шаблон можно скопировать файлом — на секреты это не влияет, там нет значений. На вашем компьютере [PowerShell], из корня репозитория:

```powershell
scp .env.example vm-re:/opt/re_web_presale/.env.example
```

Ожидаемо: `.env.example  100% ...`. (Если алиас `vm-re` не настроен, используйте `user@vm-re.simbirsoft:`.)

**Обязательный шаг: нормализация окончаний строк (на ВМ).** Файл с Windows приходит с окончаниями строк CRLF: к каждой строке добавлен невидимый символ `\r`. Из-за него значение «пусто» превращается в «один символ `\r`», проверка «задано/пусто» (8.4) показывает всё «задано», а Linux-приложения читают значения вместе с `\r`. Уберите `\r` сразу после копирования и убедитесь, что их не осталось:

```bash
sed -i 's/\r$//' /opt/re_web_presale/.env.example
grep -c $'\r' /opt/re_web_presale/.env.example
```

Ожидаемо: `0` (число строк с `\r`). Тот же шаг повторите для самого `.env` после его создания в 8.2 (команды там же): операция идемпотентна, повторный запуск безопасен. Любой файл, снова скопированный с Windows (в том числе после правок шаблона), нормализуйте так же.

### 8.2. Создать .env с безопасными правами (на ВМ)

```bash
cd /opt/re_web_presale && umask 077 && cp .env.example .env && chmod 600 .env && ls -l .env
```

Ожидаемо: `-rw------- 1 user user ... .env`.

Нормализация окончаний строк `.env` (см. 8.1; обязательно, даже если `.env.example` уже нормализован):

```bash
sed -i 's/\r$//' /opt/re_web_presale/.env
grep -c $'\r' /opt/re_web_presale/.env
```

Ожидаемо: `0`. Если число больше нуля, повторите `sed` и проверьте снова; до этого не переходите к 8.3.

### 8.3. Сгенерировать секреты на ВМ и вписать в файл

Генерируйте значения на ВМ и вставляйте напрямую в файл, не копируя в чат. Команды ниже **ничего не печатают**, а сразу записывают значения в `.env` (замена пустого значения у ключа):

```bash
cd /opt/re_web_presale
for k in POSTGRES_PASSWORD APP_RW_PASSWORD CV_RW_PASSWORD; do
  v=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
  sed -i "s|^${k}=.*|${k}=${v}|" .env
done
```

Что делает: три пароля БД (URL-safe символы, как требует шаблон).

```bash
cd /opt/re_web_presale
for k in APP_ENC_KEY JIRA_TOKEN_ENCRYPTION_KEY; do
  v=$(python3 -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())")
  sed -i "s|^${k}=.*|${k}=${v}|" .env
done
```

Что делает: два отдельных ключа шифрования (32 байта в base64). Символы `|` в base64 не встречаются, замена безопасна; `/` и `+` допустимы.

Строки подключения собираются из паролей (значения не выводятся на экран):

```bash
cd /opt/re_web_presale
A=$(grep '^APP_RW_PASSWORD=' .env | cut -d= -f2-)
C=$(grep '^CV_RW_PASSWORD=' .env | cut -d= -f2-)
sed -i "s|^APP_DB_URL=.*|APP_DB_URL=postgresql://app_rw:${A}@db:5432/app_db|" .env
sed -i "s|^CV_DB_URL=.*|CV_DB_URL=postgresql://cv_rw:${C}@db:5432/cv_db|" .env
unset A C
```

Остальные значения (не секретные) задайте в редакторе:

```bash
nano /opt/re_web_presale/.env
```

(Сохранить: Ctrl+O, Enter; выйти: Ctrl+X.) Значения (допущение; сверьте с `.env.example` и вашими параметрами).

**Обязательно заполнить:**

- `APP_ENC_KEY_ID=k1`
- `ADMIN_LOGINS=<ваш логин LDAP>`
- `LDAP_URL`, `LDAP_BIND_TEMPLATE`, `LDAP_BASE_DN` — значения от Support указаны в комментарии к блоку LDAP в `.env.example`; `LDAP_USE_TLS=false`
- `JIRA_BASE_URL=https://jira.simbirsoft.com`
- `LLM_BASE_URL=http://ollama-gpu.simbirsoft.com:3000/api/v1`
- `LLM_API_KEY` — ключ создаёт владелец; **вводите его только на ВМ в nano**, не через чат
- `SESSION_COOKIE_SECURE=true` (на проде HTTPS; пусто = true)

**Оставить пустыми** (применяются значения по умолчанию из кода, см. комментарии в `.env.example`):

- `LDAP_USER_FILTER`, `LDAP_BIND_DN`, `LDAP_BIND_PASSWORD`
- `TEST_LDAP_*` — тестовый LDAP на проде не нужен
- необязательные: `SYNC_*`, `JIRA_*_PROJECT`, `JIRA_*_ISSUETYPE`, `JIRA_UTC_OFFSET_HOURS`, `LDAP_TIMEOUT_SEC`, `SESSION_TTL_HOURS`, `LLM_MODEL`, `LLM_TASK_TTL_DAYS`, `CV_MAX_FILE_MB`, `WORKER_HEARTBEAT_PATH`, `PROXY_PORT`

### 8.4. Проверка без раскрытия значений

```bash
cd /opt/re_web_presale && ls -l .env && stat -c '%a %U' .env
```

Ожидаемо: `600 user`.

```bash
cd /opt/re_web_presale && tr -d '\r' < .env | awk -F= '/^[A-Z_]+=/ { printf "%-28s %s\n", $1, ($2=="" ? "(пусто)" : "задано") }'
```

Что делает: `tr -d '\r'` отбрасывает `\r` на лету (проверка устойчива к CRLF, файл не меняется), awk показывает имена переменных и «задано/пусто». **Значения не показываются.** Ожидаемо: пустыми могут быть только переменные из списка «оставить пустыми» в 8.3 (`TEST_LDAP_*`, `LDAP_BIND_DN`, `LDAP_BIND_PASSWORD`, `LDAP_USER_FILTER`, необязательные `SYNC_*` и т. п.). Блок Confluence можно удалить из файла.

Дополнительно проверьте отсутствие `\r` (выводится только число): `grep -c $'\r' .env`, ожидаемо `0`.

Риски:
- **Резервная копия .env обязательна:** потеря `APP_ENC_KEY` / `JIRA_TOKEN_ENCRYPTION_KEY` означает невозможность расшифровать сохранённые токены/ключи, а пароли БД не восстановить из томов. Сохраните копию файла в корпоративный менеджер паролей (как вложение/секретная заметка) — **вручную, не через чат**.
- Не выводите `.env` целиком командами `cat`/`less` при демонстрации экрана, не отправляйте скриншоты.
- Не запускайте команды с секретами в виде аргументов (`... --password xxx`): попадают в историю shell.
- Пароль PostgreSQL применяется только при первом создании тома `pgdata`; смена `.env` позже пароль в БД не изменит.

---

## 9. Чек-лист «ВМ готова к деплою»

Отмечайте по очереди; вывод каждой проверки — как выше.

| # | Проверка | Команда | Ожидаемо |
|---|---|---|---|
| 1 | Вход по ключу работает | `ssh vm-re` (с вашего ПК) | вход без пароля ВМ |
| 2 | Вход по паролю отключён | шаг 1.8, проверка | `Permission denied (publickey)` |
| 3 | Резервная копия приватного ключа и пароля `user` сохранена | вручную | да |
| 4 | Система обновлена | `sudo apt update && apt list --upgradable 2>/dev/null \| wc -l` | 1 (только заголовок) или несколько некритичных |
| 5 | Часовой пояс и NTP | `timedatectl` | нужный пояс, `synchronized: yes` |
| 6 | Файрвол | `sudo ufw status` | `active`, разрешены только 22/tcp и 443/tcp; порта 80 нет |
| 7 | Автообновления безопасности | `cat /etc/apt/apt.conf.d/20auto-upgrades` | обе строки `"1"` |
| 8 | Часовой пояс Europe/Samara | `timedatectl \| grep "Time zone"` | `Europe/Samara` |
| 9 | Docker и Compose | `docker --version && docker compose version` | версии выведены |
| 10 | Docker без sudo | `docker run --rm hello-world` | `Hello from Docker!` |
| 11 | Docker стартует при загрузке | `systemctl is-enabled docker` | `enabled` |
| 12 | Ротация логов | `docker info --format '{{.LoggingDriver}}'` | `json-file` (+ max-size 10m проверен в 5.1) |
| 13 | Каталоги и права | `ls -ld /opt/re_web_presale /var/backups/re_web_presale` | `drwxr-x---` / `drwx------`, владелец `user` |
| 14 | Диск | `df -h /` | ~130 ГБ, занято меньше 20% |
| 15 | Контроль диска | `journalctl -t disk-check -n 1 --no-pager` | запись `disk ok` |
| 16 | `.env` на месте | `stat -c '%a %U' /opt/re_web_presale/.env` | `600 user` |
| 16a | В `.env` нет окончаний строк CRLF | `grep -c $'\r' /opt/re_web_presale/.env` | `0` |
| 17 | Все обязательные переменные заданы | `tr -d '\r' < .env \| awk` из 8.4 | нет `(пусто)` у обязательных |
| 18 | Резервная копия `.env` в менеджере паролей | вручную | да |
| 19 | Исходящий доступ (для следующего шага) | `curl -sS -o /dev/null -w '%{http_code}\n' https://jira.simbirsoft.com` | любой HTTP-код (например 200/302/401); ошибка соединения = нужно открыть доступ |
| 20 | LDAP доступен | `timeout 5 bash -c '</dev/tcp/freeipa01.simbirsoft/389' && echo LDAP_PORT_OK` | `LDAP_PORT_OK` |

Если пункты 19–20 не проходят — это вопрос к Support (сетевые правила из `support_vm_request.md`, п. 3); на остальную подготовку не влияет.

Когда все пункты отмечены — напишите в чат «ВМ готова»; следующий шаг запланирован ниже.

---

## Следующие шаги (в рамках KON-49, здесь не расписаны)

1. Перенос кода на ВМ в `/opt/re_web_presale` (клонирование из репозитория/GitLab или архив) и первый запуск `docker compose` с продовой конфигурацией: TLS на nginx (443), без публикации портов БД/API, лимиты ресурсов (п. 5.2), `restart: unless-stopped`.
2. TLS-сертификат для имени `vm-re.simbirsoft` (внутренний УЦ, запрос в Support) и порядок его обновления.
3. Бэкапы (скрипт и расписание — отдельная задача KON-49): ежедневный дамп БД и копия файлов CV (30 суток), ежемесячные до 5 лет (NFR-004), лимит ≤20% диска, **проверка восстановления** на отдельной БД; еженедельная ручная выгрузка владельцем (см. раздел 6).
4. Мониторинг: проверка `/health` раз в минуту, отчёт доступности 9:00–18:00 по будням, доставка алертов (включая алерт по диску из п. 7), JSON-логи без секретов (NFR-006).
5. Инструкция отката (rollback) и проверка автоподъёма контейнера после падения.
6. Проверка изоляции (`scripts/check_db_isolation.sh`) и секретов (`scripts/check_secrets.sh`) на ВМ.

## Принятые решения (20.10.2026)

1. **Доступ:** владелец знает только пароль; инструкция по ключу сохранена как рекомендованный вариант (раздел 1), второй ключ не нужен.
2. **fail2ban:** не ставим (3.2).
3. **Подсети:** вопрос снят. Файрвол: 22/tcp и 443/tcp без ограничения по подсети, порт 80 закрыт; ограничение по подсетям можно добавить позже.
4. **Часовой пояс:** `Europe/Samara` подтверждён.
5. **Бэкапы:** хранилища вне ВМ нет. Локальные бэкапы на ВМ (30 дней + ежемесячные до 5 лет, ≤20% диска) + еженедельная ручная выгрузка дампа владельцем. Риск потери ВМ = потери локальных бэкапов принят владельцем (раздел 6).
6. **Адрес ВМ:** остаётся `vm-re.simbirsoft`; DNS-имени пока нет, для внутреннего проекта оставляем так.
7. **Объём данных:** оценка в `infra/vm.md`, 132 ГБ достаточно с большим запасом.
