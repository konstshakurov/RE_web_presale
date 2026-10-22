# Деплой приложения re_web_presale на ВМ vm-re.simbirsoft

Документ для владельца. Повторяет стиль `infra/vm_setup.md`: команды по одной,
с пометками **[PowerShell]** (с вашей машины) / **[ВМ]** (внутри ВМ по SSH),
с ожидаемым выводом. Секреты (пароли, ключи, `.env`) в этом файле не хранятся.

## 0. Предварительные условия

- VPN включён; `ssh user@vm-re.simbirsoft` работает по ключу.
- Файлы на ВМ: `/opt/re_web_presale/.env` (600, user:user), `/opt/re_web_presale/tls/`
  (cert 644, key 600), `/opt/re_web_presale/app/` (код).
- Docker Engine + Compose v2 установлены (`infra/vm_setup_report.md`).
- ufw: только 22/tcp и 443/tcp.

## 1. Повторный деплой (обновление версии)

**[PowerShell]**
```powershell
# 1. Убедитесь, что рабочее дерево чистое:
git status --short
# ожидаемо: пусто. Если есть изменения — остановитесь и разберитесь.
# 2. Зафиксируйте хеш:
git rev-parse HEAD
```

**[PowerShell]**
```powershell
# 3. Соберите архив только нужных путей (backend, frontend, src, db, infra, scripts,
#    docker-compose*.yml, .env.example, .dockerignore, .gitignore, README.md).
#    Исключены: .git, .env*, node_modules, output/, input/, estimate/, design/, qa/,
#    tests/, tests_load/, __pycache__, .claude/, docs/, skills/, phase.py, любые ключи.
tar -czf re_web_presale_deploy.tar.gz `
  backend/ frontend/ src/ db/ infra/ scripts/ `
  docker-compose.yml docker-compose.prod.yml docker-compose.migrations.yml `
  .env.example .dockerignore .gitignore README.md
# 4. Доставьте на ВМ:
scp re_web_presale_deploy.tar.gz user@vm-re.simbirsoft:/tmp/
```

**[ВМ]**
```bash
cd /opt/re_web_presale
# сохранить предыдущую версию кода (на случай отката):
mv app app.prev.$(date +%Y%m%d_%H%M%S) 2>/dev/null || true
mkdir -p app && cd app
tar -xzf /tmp/re_web_presale_deploy.tar.gz
rm -f /tmp/re_web_presale_deploy.tar.gz

# Копируем боевой оверлей и TLS-конфиг nginx из репозитория в место на ВМ:
cp docker-compose.prod.yml /opt/re_web_presale/docker-compose.prod.yml
cp infra/nginx/nginx.tls.conf /opt/re_web_presale/infra/nginx/nginx.tls.conf
# код доставлен, хеш зафиксирован в отчёте
```

**[ВМ] — миграции БД (только если есть новые миграции в db/migrations):**
```bash
cd /opt/re_web_presale/app
# применить миграции (порядок: app_db, затем cv_db):
bash db/migrations/apply.sh 2>&1 | tail -20
# ожидаемо: "migrations applied" без ошибок
```

**[ВМ] — сборка и запуск:**
```bash
cd /opt/re_web_presale/app
docker compose --env-file /opt/re_web_presale/.env \
  -f docker-compose.yml -f /opt/re_web_presale/docker-compose.prod.yml \
  build 2>&1 | tail -5
# ожидаемо: "Image ... Built" для api/web/worker

docker compose --env-file /opt/re_web_presale/.env \
  -f docker-compose.yml -f /opt/re_web_presale/docker-compose.prod.yml \
  up -d 2>&1 | tail -10
# ожидаемо: все контейнеры Created/Started
```

**[ВМ] — проверка:**
```bash
cd /opt/re_web_presale/app
docker compose --env-file /opt/re_web_presale/.env \
  -f docker-compose.yml -f /opt/re_web_presale/docker-compose.prod.yml ps
# ожидаемо: все сервисы healthy/running
curl -sk https://127.0.0.1/health
# ожидаемо: {"status":"ok"}
```

## 2. Откат на предыдущую версию

**Запрещено:** `docker compose down -v`, `docker system prune --volumes`, удаление томов БД/CV.

**[ВМ]**
```bash
cd /opt/re_web_presale
# 1. остановить текущий стенд (тома НЕ удаляются):
cd app && docker compose --env-file /opt/re_web_presale/.env \
  -f docker-compose.yml -f /opt/re_web_presale/docker-compose.prod.yml down
# 2. вернуть предыдущий код:
cd /opt/re_web_presale
rm -rf app
mv app.prev.<TIMESTAMP> app
# 3. пересобрать и поднять:
cd app
docker compose --env-file /opt/re_web_presale/.env \
  -f docker-compose.yml -f /opt/re_web_presale/docker-compose.prod.yml \
  up -d --build
```

Если миграции БД необратимы — восстановите БД из дампа (бэкапы — отдельная задача).

## 3. Просмотр логов

**[ВМ]**
```bash
cd /opt/re_web_presale/app
# все сервисы:
docker compose --env-file /opt/re_web_presale/.env \
  -f docker-compose.yml -f /opt/re_web_presale/docker-compose.prod.yml logs -f --tail=100
# один сервис:
docker compose --env-file /opt/re_web_presale/.env \
  -f docker-compose.yml -f /opt/re_web_presale/docker-compose.prod.yml logs -f api
```

## 4. Проверка состояния

**[ВМ]**
```bash
cd /opt/re_web_presale/app
docker compose --env-file /opt/re_web_presale/.env \
  -f docker-compose.yml -f /opt/re_web_presale/docker-compose.prod.yml ps
ss -tlnp | grep -E ':22 |:443 '
curl -sk https://127.0.0.1/health
```

**[PowerShell]**
```powershell
curl -k https://vm-re.simbirsoft/health
```

## 5. Обновление сертификата

Сертификат лежит в `/opt/re_web_presale/tls/` (cert 644, key 600, каталог 700).
Замените файлы `server.crt` / `server.key` и перезапустите proxy:
```bash
cd /opt/re_web_presale/app
docker compose --env-file /opt/re_web_presale/.env \
  -f docker-compose.yml -f /opt/re_web_presale/docker-compose.prod.yml \
  up -d --force-recreate proxy
```

## 6. Доверие сертификату в Windows (чтобы браузер не ругался)

Сертификат самоподписанный (см. `infra/deploy_report.md` §3). Чтобы браузер доверял ему:

1. Скопируйте сертификат с ВМ на вашу машину:
   ```powershell
   scp user@vm-re.simbirsoft:/opt/re_web_presale/tls/server.crt $env:TEMP\vm-re.crt
   ```
2. Откройте `certmgr.msc` → «Доверенные корневые центры сертификации» → «Сертификаты».
3. ПКМ → «Все задачи» → «Импорт» → выберите `$env:TEMP\vm-re.crt`.
4. Подтвердите предупреждение о безопасности.
5. Перезапустите браузер. `https://vm-re.simbirsoft` откроется без предупреждения.

**Позже (замена на сертификат внутреннего УЦ):** получите сертификат от внутреннего УЦ Simbirsoft
(с SAN `vm-re.simbirsoft`, `vm-re`, `10.73.11.110`), замените файлы в `/opt/re_web_presale/tls/`
(п. 5) и удалите самоподписанный из доверенных корневых.

**HSTS:** не включён намеренно (сертификат самоподписанный). Включить позже, после перехода
на внутренний УЦ — раскомментируйте `add_header Strict-Transport-Security ...` в
`/opt/re_web_presale/infra/nginx/nginx.tls.conf` и пересоздайте proxy.
