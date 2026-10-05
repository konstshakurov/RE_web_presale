#!/bin/bash
# Создаёт две БД и две роли с взаимной изоляцией (ADR-003, NFR-007).
# Запускается официальным entrypoint только при первой инициализации тома.
# Пароли берутся из окружения контейнера; в файле и логах их нет.
set -euo pipefail

: "${APP_RW_PASSWORD:?APP_RW_PASSWORD не задан}"
: "${CV_RW_PASSWORD:?CV_RW_PASSWORD не задан}"

psql -v ON_ERROR_STOP=1 -X --username "$POSTGRES_USER" --dbname postgres \
  -v app_pw="$APP_RW_PASSWORD" -v cv_pw="$CV_RW_PASSWORD" <<'SQL' >/dev/null
SELECT format('CREATE ROLE app_rw LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE', :'app_pw') \gexec
SELECT format('CREATE ROLE cv_rw LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE', :'cv_pw') \gexec
CREATE DATABASE app_db OWNER app_rw;
CREATE DATABASE cv_db OWNER cv_rw;
-- по умолчанию CONNECT выдан PUBLIC: отзываем и выдаём только владельцу
REVOKE ALL ON DATABASE app_db FROM PUBLIC;
REVOKE ALL ON DATABASE cv_db FROM PUBLIC;
GRANT CONNECT ON DATABASE app_db TO app_rw;
GRANT CONNECT ON DATABASE cv_db TO cv_rw;
SQL

# Необязательные миграции (каталог db/migrations принадлежит KON-29; монтируется оверлеем
# docker-compose.migrations.yml). Раскладка: /migrations/app_db/V*.sql и /migrations/cv_db/V*.sql.
# Выполняются суперпользователем (файлы grants сами выдают права ролям); *.down.sql пропускаются.
# /migrations/bootstrap не используется: роли и БД уже созданы выше из env.
for db in app_db cv_db; do
  dir="/migrations/${db}"
  [ -d "$dir" ] || continue
  for f in $(ls "$dir"/V*.sql 2>/dev/null | grep -v '\.down\.sql$' | sort); do
    echo "migration ${db}: $(basename "$f")"
    psql -v ON_ERROR_STOP=1 -X --username "$POSTGRES_USER" --dbname "$db" -f "$f" >/dev/null
  done
done
