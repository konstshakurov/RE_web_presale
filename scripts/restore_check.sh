#!/usr/bin/env bash
# Проверка восстановления бэкапа во ВРЕМЕННУЮ БД (KON-49, NFR-004). Боевые БД не трогает:
# создаёт restore_check_<время>, разворачивает туда дамп, считает таблицы и строки, удаляет её.
# Использование: restore_check.sh [файл.dump ...]  (без аргументов - последний дамп каждой БД)
# Код выхода 0 - все проверки пройдены.
set -uo pipefail
COMPONENT=restore_check
source "$(dirname "$0")/ops_common.sh"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/re_web_presale}"
BACKUP_DBS="${BACKUP_DBS:-app_db cv_db}"
fail=0

psql_() { "${EXEC[@]}" psql -U postgres -v ON_ERROR_STOP=1 -X -tA "$@"; }
Q_TABLES="select count(*) from information_schema.tables where table_schema not in ('pg_catalog','information_schema') and table_type='BASE TABLE'"
Q_ROWS="select coalesce(sum((xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', table_schema, table_name), false, true, '')))[1]::text::bigint),0) from information_schema.tables where table_schema not in ('pg_catalog','information_schema') and table_type='BASE TABLE'"

check_file() {
  local file="$1" db tmpdb tables rows live errf rc=0
  db="$(basename "$file" | sed -E 's/_[0-9]{6,8}(_[0-9]{6})?\.dump$//')"
  if [ -f "$file.sha256" ] && ! (cd "$(dirname "$file")" && sha256sum -c --quiet "$(basename "$file").sha256" >/dev/null 2>&1); then
    alert "restore_check: контрольная сумма $(basename "$file") не сошлась"; return 1
  fi
  tmpdb="restore_check_$(date +%s)_$$"
  psql_ -d postgres -c "CREATE DATABASE \"$tmpdb\"" >/dev/null || { alert "restore_check: не создана БД $tmpdb"; return 1; }
  errf="$(mktemp)"
  if ! "${EXEC[@]}" pg_restore -U postgres -d "$tmpdb" --no-owner --no-acl --exit-on-error <"$file" >/dev/null 2>"$errf"; then
    alert "restore_check: pg_restore $(basename "$file") упал: $(head -c 300 "$errf")"; rc=1
  else
    tables="$(psql_ -d "$tmpdb" -c "$Q_TABLES")"
    rows="$(psql_ -d "$tmpdb" -c "$Q_ROWS")"
    live="$(psql_ -d "$db" -c "$Q_TABLES" 2>/dev/null || echo '?')"
    if [ "${tables:-0}" -eq 0 ]; then alert "restore_check: $db восстановлена без таблиц"; rc=1
    else
      log info restore_ok "$(basename "$file"): таблиц=$tables (в боевой БД: $live), строк=$rows"
      if [ "$tables" != "$live" ]; then log warn tables_differ "число таблиц в дампе и в боевой БД различается (возможны миграции после дампа)"; fi
    fi
  fi
  rm -f "$errf"
  case "$tmpdb" in restore_check_*) psql_ -d postgres -c "DROP DATABASE \"$tmpdb\"" >/dev/null || log warn drop_failed "$tmpdb" ;; esac
  return $rc
}

if [ $# -ge 1 ]; then files=("$@"); else
  files=()
  for db in $BACKUP_DBS; do
    f="$(ls -1t "$BACKUP_DIR/daily/${db}_"*.dump 2>/dev/null | head -n1 || true)"
    if [ -z "$f" ]; then alert "restore_check: нет дампов $db в $BACKUP_DIR/daily"; fail=1; continue; fi
    files+=("$f")
  done
fi
for f in "${files[@]}"; do check_file "$f" || fail=1; done
if [ "$fail" -eq 0 ]; then log info done "восстановление проверено"; else log error done "есть ошибки"; fi
exit "$fail"
