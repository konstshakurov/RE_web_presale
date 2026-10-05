#!/usr/bin/env bash
# Проверка изоляции ролей (ADR-003, NFR-007): cv_rw не видит app_db и наоборот.
# Требует запущенный сервис db: docker compose up -d db
# Пароли берутся из окружения контейнера db; в вывод не попадают.
set -uo pipefail

COMPOSE="${COMPOSE:-docker compose}"
fail=0

# $1 роль, $2 БД, $3 ожидание: ok|deny
try_connect() {
  local role="$1" db="$2" expect="$3" pwvar out rc
  case "$role" in
    app_rw) pwvar=APP_RW_PASSWORD ;;
    cv_rw)  pwvar=CV_RW_PASSWORD ;;
  esac
  # -h 127.0.0.1: парольная аутентификация (по сокету в образе стоит trust)
  out=$($COMPOSE exec -T db sh -c \
    "PGPASSWORD=\"\$$pwvar\" psql -h 127.0.0.1 -U $role -d $db -tAc 'select 1'" 2>&1)
  rc=$?
  if [ "$expect" = ok ] && [ $rc -eq 0 ]; then
    echo "OK    $role -> $db: доступ есть (ожидалось)"
  elif [ "$expect" = deny ] && [ $rc -ne 0 ] && echo "$out" | grep -qi "permission denied\|not permitted\|FATAL"; then
    echo "OK    $role -> $db: доступа нет (ожидалось)"
  else
    echo "FAIL  $role -> $db: ожидалось $expect, rc=$rc"
    fail=1
  fi
}

try_connect app_rw app_db ok
try_connect cv_rw  cv_db  ok
try_connect app_rw cv_db  deny
try_connect cv_rw  app_db deny

# Роли не суперпользователи и не создают БД/роли
bad=$($COMPOSE exec -T db psql -U postgres -tAc \
  "select rolname from pg_roles where rolname in ('app_rw','cv_rw') and (rolsuper or rolcreatedb or rolcreaterole)")
if [ -n "$bad" ]; then echo "FAIL  привилегированные роли: $bad"; fail=1; else echo "OK    app_rw/cv_rw без привилегий суперпользователя"; fi

if [ $fail -eq 0 ]; then echo "ИТОГ: изоляция ролей подтверждена"; else echo "ИТОГ: ОШИБКА изоляции"; fi
exit $fail
