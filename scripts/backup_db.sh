#!/usr/bin/env bash
# Ежедневный pg_dump app_db и cv_db с ротацией (KON-49, NFR-004, A6).
# Ежедневные копии: BACKUP_DAILY_DAYS (30). Ежемесячные (первый успешный дамп месяца):
# BACKUP_MONTHLY_DAYS (1900 ~ 5 лет и 2 месяца). Ежемесячные при нехватке места не удаляются
# (вместо этого алерт).
# Окружение (все необязательны): BACKUP_DIR, DB_CONTAINER | DB_EXEC, BACKUP_DBS, BACKUP_MAX_GB,
# ALERT_WEBHOOK_URL, ALERT_CMD.
set -euo pipefail
COMPONENT=backup
source "$(dirname "$0")/ops_common.sh"

BACKUP_DIR="${BACKUP_DIR:-/var/backups/re_web_presale}"
BACKUP_DBS="${BACKUP_DBS:-app_db cv_db}"
BACKUP_DAILY_DAYS="${BACKUP_DAILY_DAYS:-30}"
BACKUP_MONTHLY_DAYS="${BACKUP_MONTHLY_DAYS:-1900}"
BACKUP_MAX_GB="${BACKUP_MAX_GB:-26}"   # <=20% диска 132 ГБ (infra/vm_setup.md, раздел 6)

umask 077
mkdir -p "$BACKUP_DIR"/daily "$BACKUP_DIR"/monthly "$BACKUP_DIR"/.tmp
exec 9>"$BACKUP_DIR/.lock"
flock -n 9 || { log error locked "другой бэкап уже идёт"; exit 2; }

stamp="$(date +%Y%m%d_%H%M%S)"; month="$(date +%Y%m)"
fail=0
for db in $BACKUP_DBS; do
  part="$BACKUP_DIR/.tmp/${db}_${stamp}.dump.part"
  final="$BACKUP_DIR/daily/${db}_${stamp}.dump"
  if ! "${EXEC[@]}" pg_dump -U postgres -Fc "$db" >"$part" 2>"$BACKUP_DIR/.tmp/err.txt"; then
    alert "pg_dump $db не удался: $(head -c 300 "$BACKUP_DIR/.tmp/err.txt")"; rm -f "$part"; fail=1; continue
  fi
  # целостность: pg_restore должен прочитать оглавление архива
  if [ ! -s "$part" ] || ! "${EXEC[@]}" pg_restore -l <"$part" >/dev/null 2>&1; then
    alert "дамп $db повреждён или пуст"; rm -f "$part"; fail=1; continue
  fi
  mv "$part" "$final"
  (cd "$BACKUP_DIR/daily" && sha256sum "$(basename "$final")" >"$(basename "$final").sha256")
  if ! ls "$BACKUP_DIR/monthly/${db}_${month}"*.dump >/dev/null 2>&1; then
    cp "$final" "$BACKUP_DIR/monthly/${db}_${month}.dump"
    (cd "$BACKUP_DIR/monthly" && sha256sum "${db}_${month}.dump" >"${db}_${month}.dump.sha256")
    log info monthly_created "${db}_${month}.dump"
  fi
  log info dump_ok "$db $(stat -c %s "$final") байт"
done

# ротация
find "$BACKUP_DIR/daily"   -type f -mtime +"$BACKUP_DAILY_DAYS"   -name '*.dump*' -delete
find "$BACKUP_DIR/monthly" -type f -mtime +"$BACKUP_MONTHLY_DAYS" -name '*.dump*' -delete
find "$BACKUP_DIR/.tmp" -type f -name '*.part' -mmin +720 -delete

# лимит места: только алерт, ничего дополнительно не удаляем
used_kb="$(du -sk "$BACKUP_DIR" | cut -f1)"
if [ "$used_kb" -gt $((BACKUP_MAX_GB * 1024 * 1024)) ]; then
  alert "бэкапы заняли $((used_kb / 1048576)) ГБ из лимита $BACKUP_MAX_GB ГБ; ежемесячные не удалены"
  fail=1
fi

if [ "$fail" -eq 0 ]; then date +%s >"$BACKUP_DIR/last_success"; log info done "ok"; fi
exit "$fail"
