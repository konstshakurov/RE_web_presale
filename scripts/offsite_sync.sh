#!/usr/bin/env bash
# Внешняя копия бэкапов в облако через rclone (Google Диск владельца) - НЕОБЯЗАТЕЛЬНЫЙ шаг (KON-49).
# Включается переменной OFFSITE_RCLONE_REMOTE (например gdrive:re_web_presale_backups); пусто = выключено,
# скрипт пишет в журнал и выходит с кодом 0.
# Порядок: проверка сумм -> restore-check последних дампов во временную БД -> выгрузка новых файлов
# (rclone copy) -> проверка (rclone check) -> ротация на стороне облака по тем же срокам -> отметка успеха.
# Выгружаются: все ежемесячные дампы и последний ежедневный каждой БД (+ .sha256). Уже выгруженное повторно
# не передаётся. Любой сбой - алерт и код 1; локальные бэкапы это не затрагивает.
# ВНИМАНИЕ: дампы содержат персональные данные сотрудников (CV). Шифрование перед выгрузкой - OFFSITE_ENCRYPT:
#   none  - без шифрования (требует явного OFFSITE_ALLOW_PLAINTEXT=1: решение владельца/безопасности);
#   crypt - OFFSITE_RCLONE_REMOTE указывает на rclone-remote типа crypt (шифрует rclone, ключи в rclone.conf);
#   gpg   - файлы шифруются публичным ключом OFFSITE_GPG_RECIPIENT на ВМ; закрытый ключ хранится отдельно от Диска и ВМ.
# Окружение: OFFSITE_RCLONE_REMOTE, OFFSITE_ENCRYPT (none), OFFSITE_ALLOW_PLAINTEXT, OFFSITE_GPG_RECIPIENT,
# OFFSITE_RESTORE_CHECK (1), OFFSITE_RCLONE_FLAGS, RCLONE_BIN (rclone), BACKUP_DIR, BACKUP_DBS,
# BACKUP_DAILY_DAYS (30), BACKUP_MONTHLY_DAYS (1900), SMTP_*/ALERT_*.
set -uo pipefail
COMPONENT=offsite
source "$(dirname "$0")/ops_common.sh"

REMOTE="${OFFSITE_RCLONE_REMOTE:-}"
if [ -z "$REMOTE" ]; then log info offsite_disabled "OFFSITE_RCLONE_REMOTE не задан, внешняя копия выключена"; exit 0; fi
REMOTE="${REMOTE%/}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/re_web_presale}"
BACKUP_DBS="${BACKUP_DBS:-app_db cv_db}"
BACKUP_DAILY_DAYS="${BACKUP_DAILY_DAYS:-30}"
BACKUP_MONTHLY_DAYS="${BACKUP_MONTHLY_DAYS:-1900}"
MODE="${OFFSITE_ENCRYPT:-none}"
RCLONE="${RCLONE_BIN:-rclone}"
read -ra RFLAGS <<<"${OFFSITE_RCLONE_FLAGS:-}"

fail() { alert "offsite: $1"; exit 1; }

umask 077
command -v "$RCLONE" >/dev/null 2>&1 || fail "rclone не установлен"
[ -d "$BACKUP_DIR" ] || fail "нет каталога бэкапов $BACKUP_DIR"
exec 9>"$BACKUP_DIR/.offsite.lock"
flock -n 9 || { log error locked "другая выгрузка уже идёт"; exit 2; }

# шифрование: проверки конфигурации до любых действий
case "$MODE" in
  none)
    [ "${OFFSITE_ALLOW_PLAINTEXT:-}" = 1 ] || fail "OFFSITE_ENCRYPT=none: выгрузка персональных данных без шифрования не разрешена (нужно решение владельца: OFFSITE_ALLOW_PLAINTEXT=1 или включить шифрование)"
    log warn offsite_plaintext "дампы уходят в облако БЕЗ шифрования (разрешено OFFSITE_ALLOW_PLAINTEXT=1)" ;;
  crypt)
    rname="${REMOTE%%:*}"
    "$RCLONE" config dump 2>/dev/null | python3 -I -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get(sys.argv[1],{}).get("type")=="crypt" else 1)' "$rname" \
      || fail "OFFSITE_ENCRYPT=crypt, но remote '$rname' не типа crypt" ;;
  gpg)
    command -v gpg >/dev/null 2>&1 || fail "gpg не установлен"
    [ -n "${OFFSITE_GPG_RECIPIENT:-}" ] || fail "OFFSITE_GPG_RECIPIENT не задан"
    gpg --batch --list-keys "$OFFSITE_GPG_RECIPIENT" >/dev/null 2>&1 || fail "публичный ключ '$OFFSITE_GPG_RECIPIENT' не найден в связке gpg пользователя" ;;
  *) fail "неизвестное значение OFFSITE_ENCRYPT=$MODE (none|crypt|gpg)" ;;
esac

STAGE="$BACKUP_DIR/.tmp/offsite"
LIST="$(mktemp)"
trap 'rm -rf "$STAGE" "$LIST"' EXIT
rm -rf "$STAGE"; mkdir -p "$STAGE"

# 1. кандидаты: последний ежедневный каждой БД + все ежемесячные
cand=(); latest=()
for db in $BACKUP_DBS; do
  f="$(ls -1t "$BACKUP_DIR/daily/${db}_"*.dump 2>/dev/null | head -n1 || true)"
  [ -n "$f" ] || fail "нет ежедневных дампов $db"
  latest+=("$f"); cand+=("daily/$(basename "$f")")
done
for f in "$BACKUP_DIR"/monthly/*.dump; do [ -e "$f" ] && cand+=("monthly/$(basename "$f")"); done

# 2. целостность по .sha256
for rel in "${cand[@]}"; do
  if [ -f "$BACKUP_DIR/$rel.sha256" ]; then
    (cd "$BACKUP_DIR/$(dirname "$rel")" && sha256sum -c --quiet "$(basename "$rel").sha256" >/dev/null 2>&1) \
      || fail "контрольная сумма $rel не сошлась, выгрузка отменена"
  fi
done

# 3. restore-check последних дампов (в одноразовую БД): в облако уходит только то, что восстанавливается
if [ "${OFFSITE_RESTORE_CHECK:-1}" != 0 ]; then
  bash "$OPS_DIR/restore_check.sh" "${latest[@]}" >/dev/null \
    || fail "restore-check последнего дампа не пройден, выгрузка отменена"
  log info restore_checked "последние дампы восстанавливаются"
fi

# 4. что уже есть в облаке
"$RCLONE" mkdir "${RFLAGS[@]}" "$REMOTE" >/dev/null 2>&1 || fail "облако недоступно (rclone mkdir $REMOTE)"
remote_list="$("$RCLONE" lsf -R --files-only "${RFLAGS[@]}" "$REMOTE" 2>/dev/null)" || fail "не удалось получить список файлов в облаке"

# 5. что выгружать (gpg: шифруем во временный каталог; mtime сохраняется для ротации в облаке)
SRC="$BACKUP_DIR"; ext=""
if [ "$MODE" = gpg ]; then SRC="$STAGE"; ext=".gpg"; fi
new=0
for rel in "${cand[@]}"; do
  name="$rel$ext"
  if printf '%s\n' "$remote_list" | grep -qxF "$name"; then continue; fi
  if [ "$MODE" = gpg ]; then
    mkdir -p "$STAGE/$(dirname "$rel")"
    gpg --batch --yes --trust-model always --compress-algo none -r "$OFFSITE_GPG_RECIPIENT" \
        -o "$STAGE/$name" --encrypt "$BACKUP_DIR/$rel" 2>/dev/null || fail "gpg не смог зашифровать $rel"
    touch -r "$BACKUP_DIR/$rel" "$STAGE/$name"
    (cd "$STAGE/$(dirname "$rel")" && sha256sum "$(basename "$name")" >"$(basename "$name").sha256")
    touch -r "$BACKUP_DIR/$rel" "$STAGE/$name.sha256"
    printf '%s\n%s\n' "$name" "$name.sha256" >>"$LIST"
  else
    printf '%s\n' "$rel" >>"$LIST"
    [ -f "$BACKUP_DIR/$rel.sha256" ] && printf '%s\n' "$rel.sha256" >>"$LIST"
  fi
  new=$((new + 1))
done

# 6. выгрузка и проверка
if [ "$new" -gt 0 ]; then
  "$RCLONE" copy "${RFLAGS[@]}" --files-from "$LIST" --retries 3 "$SRC" "$REMOTE" >/dev/null 2>"$STAGE/err.txt" \
    || fail "rclone copy не удался: $(head -c 300 "$STAGE/err.txt" | tr '\n' ' ')"
  "$RCLONE" check "${RFLAGS[@]}" --files-from "$LIST" --one-way "$SRC" "$REMOTE" >/dev/null 2>"$STAGE/err.txt" \
    || fail "rclone check: выгруженные файлы не совпадают с локальными: $(head -c 300 "$STAGE/err.txt" | tr '\n' ' ')"
  log info offsite_uploaded "новых файлов: $new, проверено rclone check"
else
  log info offsite_uptodate "новых файлов для выгрузки нет"
fi

# 7. ротация в облаке по тем же срокам (только после успешной выгрузки)
"$RCLONE" delete "${RFLAGS[@]}" --min-age "${BACKUP_DAILY_DAYS}d" "$REMOTE/daily" >/dev/null 2>&1 \
  || log warn offsite_rotate_skipped "daily: каталога нет или ошибка"
"$RCLONE" delete "${RFLAGS[@]}" --min-age "${BACKUP_MONTHLY_DAYS}d" "$REMOTE/monthly" >/dev/null 2>&1 \
  || log warn offsite_rotate_skipped "monthly: каталога нет или ошибка"

date +%s >"$BACKUP_DIR/offsite_last_success"
log info done "ok (режим шифрования: $MODE)"
exit 0
