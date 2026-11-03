#!/usr/bin/env bash
# Деплой фиксированной версии re_web_presale на ВМ (KON-123).
#
# Запуск (обычно вызывает scripts/deploy.ps1, но можно вручную):
#   bash /tmp/re_deploy.sh <git_hash> <archive.tar.gz> [KEEP_PREV]
#
# Что делает:
#   1. блокирует параллельные запуски (flock),
#   2. короткое замыкание: та же версия уже на стенде — ничего не делает,
#   3. распаковывает архив во временную папку, проверяет отсутствие CRLF,
#   4. сохраняет предыдущую версию (app.prev.<время>),
#   5. при изменении миграций: дамп БД (механизм KON-49) + db/migrations/apply.sh,
#   6. build + up -d боевого оверлея,
#   7. ждёт health {"status":"ok"} и healthy у всех сервисов,
#   8. при сбое — автоматический откат на app.prev (тома НЕ удаляются),
#   9. пишет журнал в /opt/re_web_presale/deploy.log и DEPLOYED_VERSION.
#
# Запрещено внутри скрипта: docker compose down -v, docker system prune,
# удаление томов БД/CV, печать значений .env.
set -euo pipefail

BASE=/opt/re_web_presale
APP="$BASE/app"
LOG="$BASE/deploy.log"
LOCK="$BASE/.deploy.lock"
VERSION_FILE="$BASE/DEPLOYED_VERSION"
OVERLAY="$BASE/docker-compose.prod.yml"
OVERLAY_CONF="$BASE/infra/nginx/nginx.tls.conf"
HEALTH_URL="https://127.0.0.1/health"
HEALTH_EXPECT='{"status":"ok"}'
HEALTH_TIMEOUT_SEC=300
KEEP_PREV_N="${3:-3}"

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <git_hash> <archive.tar.gz> [KEEP_PREV]" >&2
  exit 64
fi

HASH="$1"
ARCHIVE="$2"

now() { date '+%Y-%m-%d %T'; }
journal() { echo "[$(now)] $*" | tee -a "$LOG"; }
fail() { journal "FAIL $*"; exit "${2:-1}"; }

# --- аргументы ---
case "$HASH" in
  [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) ;; # короткий/полный sha
  *) echo "deploy: HASH должен быть git sha ($HASH)" >&2; exit 64 ;;
esac
[ -f "$ARCHIVE" ] || fail "архив не найден: $ARCHIVE" 64

# --- предусловия ---
[ -s "$BASE/.env" ] || fail "нет /opt/re_web_presale/.env" 4
[ -f "$BASE/tls/server.crt" ] && [ -f "$BASE/tls/server.key" ] || fail "нет tls/ сертификатов" 4
command -v docker >/dev/null 2>&1 || fail "docker недоступен" 4
command -v curl >/dev/null 2>&1 || fail "curl недоступен" 4

# --- блокировка параллельных запусков (критерий: второй запуск отклоняется) ---
exec 9>"$LOCK"
if ! flock -n 9; then
  journal "REJECT параллельный запуск отклонён (held lock: $LOCK)" && exit 3
fi

# --- короткое замыкание: та же версия (критерий: повтор безопасен, ничего не пересоздаётся) ---
if [ -f "$VERSION_FILE" ] && [ "$(cat "$VERSION_FILE" 2>/dev/null)" = "$HASH" ]; then
  journal "SKIP версия $HASH уже на стенде — ничего не делаем"
  echo "DEPLOYED_VERSION=$HASH (изменений нет)"
  exit 0
fi

START=$(date +%s)
PREV="app.prev.$(date +%Y%m%d_%H%M%S)"

compose() {
  docker compose --env-file "$BASE/.env" \
    -f "$APP/docker-compose.yml" -f "$OVERLAY" \
    "$@"
}

rollback() {
  journal "ROLLBACK начинаю откат на $PREV"
  compose logs --tail=120 api worker >>"$LOG" 2>&1 || true   # логи упавших контейнеров в журнал
  compose down >/dev/null 2>&1 || true        # тома НЕ удаляются (без -v)
  rm -rf "$APP"
  mv "$BASE/$PREV" "$APP"
  if [ -f "$BASE/DEPLOYED_VERSION.prev" ]; then
    mv "$BASE/DEPLOYED_VERSION.prev" "$VERSION_FILE"
  else
    rm -f "$VERSION_FILE"
  fi
  if compose up -d --build >/dev/null 2>&1; then
    journal "ROLLBACK-ok предыдущая версия поднята ($PREV)"
  else
    journal "ROLLBACK-FAIL не удалось поднять предыдущую версию — смотреть docker compose ps / logs"
  fi
}

# --- распаковка во временную папку + проверка CRLF до смены app/ ---
STAGE=$(mktemp -d /tmp/re_deploy.XXXXXX)
if ! tar -xzf "$ARCHIVE" -C "$STAGE"; then
  rm -rf "$STAGE"
  fail "tar: не удалось распаковать архив" 4
fi
if grep -rlI $'\r' \
     --include='*.sh' --include='*.yml' --include='*.yaml' \
     --include='*.conf' --include='Dockerfile*' --include='.dockerignore' \
     "$STAGE" >/dev/null 2>&1; then
  grep -rlI $'\r' --include='*.sh' --include='*.yml' --include='*.conf' "$STAGE" | sed 's/^/  CRLF: /'
  rm -rf "$STAGE"
  fail "в архиве есть файлы с CRLF (см. .gitattributes) — деплой остановлен" 4
fi

# --- смена версии: прежняя уходит в app.prev.<время> ---
if [ -d "$APP" ]; then
  mv "$APP" "$BASE/$PREV"
else
  PREV=""
fi
mkdir -p "$APP"
cp -a "$STAGE/." "$APP/"
rm -rf "$STAGE"

cp -f "$APP/docker-compose.prod.yml" "$OVERLAY"
mkdir -p "$(dirname "$OVERLAY_CONF")"
cp -f "$APP/infra/nginx/nginx.tls.conf" "$OVERLAY_CONF"

# зафиксировать план: версия + куда откатываться
mv "$VERSION_FILE" "$BASE/DEPLOYED_VERSION.prev" 2>/dev/null || true
echo "$HASH" > "$VERSION_FILE.tmp" && mv "$VERSION_FILE.tmp" "$VERSION_FILE"

journal "START hash=$HASH prev=$PREV"

# --- миграции: только если изменились (дамп — механизм KON-49) ---
DB_CHANGED=0
if [ -n "$PREV" ] && [ -d "$BASE/$PREV/db/migrations" ]; then
  diff -qr "$BASE/$PREV/db/migrations" "$APP/db/migrations" >/dev/null 2>&1 || DB_CHANGED=1
elif [ ! -d "$BASE/$PREV" ]; then
  DB_CHANGED=1 # первый деплой - применяем всё
fi
if [ "$DB_CHANGED" = "1" ]; then
  journal "MIGRATE обнаружены изменения db/migrations"
  # дамп перед миграцией - механизм KON-49; скрипты живут в app/scripts (ExecStart systemd)
  if [ -f "$APP/scripts/backup_db.sh" ]; then
    if (
      cd "$APP"
      if [ -f "$BASE/ops.env" ]; then set -a; . "$BASE/ops.env"; set +a; fi
      bash scripts/backup_db.sh
    ) >>"$LOG" 2>&1; then
      journal "MIGRATE дамп перед миграцией выполнен"
    else
      journal "MIGRATE дамп перед миграцией не удался (продолжаю: есть дневной бэкап)"
    fi
  else
    journal "MIGRATE backup_db.sh не найден (app/scripts) — дамп пропущен (опираюсь на дневные бэкапы)"
  fi
  # apply.sh требует аргументы: apply.sh up|down app_db|cv_db [N]; psql - внутри контейнера db
  DB_CT=$(docker compose --env-file "$BASE/.env" -f "$APP/docker-compose.yml" -f "$OVERLAY" ps -q db)
  if [ -z "$DB_CT" ]; then
    rollback
    fail "MIGRATE контейнер db не найден (compose ps -q db пуст)" 5
  fi
  export PSQL="docker exec -i $DB_CT psql -U postgres"
  if ! ( cd "$APP"
         bash db/migrations/apply.sh up app_db ) >>"$LOG" 2>&1; then
    rollback
    fail "MIGRATE apply.sh up app_db упал — восстановлена предыдущая версия" 5
  fi
  if ! ( cd "$APP"
         bash db/migrations/apply.sh up cv_db ) >>"$LOG" 2>&1; then
    rollback
    fail "MIGRATE apply.sh up cv_db упал — восстановлена предыдущая версия" 5
  fi
  journal "MIGRATE применены (app_db, cv_db)"
fi

# --- сборка и запуск ---
if ! compose build >>"$LOG" 2>&1; then
  rollback
  fail "BUILD docker compose build упал — восстановлена предыдущая версия" 6
fi
if ! compose up -d >>"$LOG" 2>&1; then
  rollback
  fail "UP docker compose up -d упал — восстановлена предыдущая версия" 7
fi

# --- проверка здоровья: /health == {"status":"ok"} и healthy у всех сервисов ---
SVC="db api worker web proxy"
check_all() {
  [ "$(curl -sk --max-time 4 "$HEALTH_URL" 2>/dev/null)" = "$HEALTH_EXPECT" ] || return 1
  local s
  for s in $SVC; do
    [ "$(docker inspect --format '{{.State.Health.Status}}' re_web_presale-${s}-1 2>/dev/null)" = "healthy" ] || return 1
  done
}

HEALTH_OK=0
i=0
while [ "$i" -lt "$HEALTH_TIMEOUT_SEC" ]; do
  if check_all; then HEALTH_OK=1; break; fi
  sleep 5
  i=$((i+5))
done
if [ "$HEALTH_OK" != "1" ]; then
  compose ps >>"$LOG" 2>&1 || true
  rollback
  fail "HEALTH /health или контейнеры не стали healthy за ${HEALTH_TIMEOUT_SEC}s — выполнен откат" 8
fi

# --- ретеншен: хранить последние KEEP_PREV_N версий app.prev.* ---
if [ "${KEEP_PREV_N:-3}" -gt 0 ] 2>/dev/null; then
  ls -dt "$BASE"/app.prev.* 2>/dev/null | tail -n +$((KEEP_PREV_N+1)) | while IFS= read -r d; do
    rm -rf "$d"
    journal "RETAIN удалена старая копия $(basename "$d")"
  done
fi

DUR=$(( $(date +%s) - START ))
journal "OK hash=$HASH duration=${DUR}s prev=$PREV"
echo "deploy ok: $HASH (см. $LOG)"
