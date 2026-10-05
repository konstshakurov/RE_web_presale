#!/usr/bin/env bash
# Применение версионированных миграций без привязки к ORM.
#   apply.sh up   <app_db|cv_db>            применить все непримененные V*.sql по порядку
#   apply.sh down <app_db|cv_db> [N]        откатить последние N миграций (по умолчанию 1)
# Подключение задаётся переменной PSQL (по умолчанию: psql с PG* переменными окружения), например:
#   PSQL="psql -h localhost -U postgres" ./apply.sh up app_db
#   PSQL="docker exec -i pgtest psql -U postgres" ./apply.sh up cv_db
# Миграции выполняются под владельцем БД (суперпользователь/migrator), не под app_rw/cv_rw.
# Предварительно один раз выполняется bootstrap/00_roles_and_databases.sql.
set -euo pipefail

CMD="${1:?usage: apply.sh up|down app_db|cv_db [N]}"
DB="${2:?usage: apply.sh up|down app_db|cv_db [N]}"
N="${3:-1}"
PSQL="${PSQL:-psql}"
DIR="$(cd "$(dirname "$0")" && pwd)/$DB"

run_sql() { $PSQL -X -q -v ON_ERROR_STOP=1 --single-transaction -d "$DB" < "$1"; }
query()   { $PSQL -X -q -t -A -v ON_ERROR_STOP=1 -d "$DB" -c "$1"; }

applied_versions() { query "SELECT version FROM meta.schema_migration ORDER BY version" 2>/dev/null || true; }

case "$CMD" in
  up)
    for f in $(ls "$DIR"/V*.sql | grep -v '\.down\.sql$' | sort); do
      v="$(basename "$f" | cut -d_ -f1)"
      if applied_versions | grep -qx "$v"; then echo "skip   $DB $v"; continue; fi
      echo "apply  $DB $(basename "$f")"
      run_sql "$f"
    done ;;
  down)
    for v in $(applied_versions | sort -r | head -n "$N"); do
      f="$(ls "$DIR"/${v}__*.down.sql)"
      echo "revert $DB $(basename "$f")"
      run_sql "$f"
    done ;;
  *) echo "unknown command: $CMD" >&2; exit 2 ;;
esac
