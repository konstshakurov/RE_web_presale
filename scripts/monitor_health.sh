#!/usr/bin/env bash
# Проверка /health раз в минуту (KON-49, NFR-003) + состояние контейнеров + свежесть бэкапа.
# Пишет JSON-строки в $MONITOR_DIR/health-YYYYMM.jsonl (источник отчёта доступности).
# Окружение: HEALTH_URL (https://127.0.0.1/health), MONITOR_DIR, HEALTH_FAIL_THRESHOLD (3),
# COMPOSE_PROJECT (re_web_presale), AUTOHEAL=1 (перезапуск unhealthy-контейнера; в ops.env.example включён,
# без переменной - 0), AUTOHEAL_MAX_PER_HOUR (3: не чаще N перезапусков одного контейнера в час, дальше только алерт),
# BACKUP_DIR, BACKUP_MAX_AGE_H (36), OFFSITE_RCLONE_REMOTE/OFFSITE_MAX_AGE_H (48: свежесть внешней копии),
# NOTIFY_RECOVERY, SMTP_*/ALERT_EMAIL_TO, ALERT_WEBHOOK_URL, ALERT_CMD.
set -uo pipefail
COMPONENT=monitor
source "$(dirname "$0")/ops_common.sh"
HEALTH_URL="${HEALTH_URL:-https://127.0.0.1/health}"
MONITOR_DIR="${MONITOR_DIR:-/var/lib/re_web_presale/monitor}"
THRESH="${HEALTH_FAIL_THRESHOLD:-3}"
PROJECT="${COMPOSE_PROJECT:-re_web_presale}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/re_web_presale}"
BACKUP_MAX_AGE_H="${BACKUP_MAX_AGE_H:-36}"
mkdir -p "$MONITOR_DIR/state"
exec 9>"$MONITOR_DIR/.lock"; flock -n 9 || exit 0
# alert_state KEY BAD(0|1) MESSAGE: алерт один раз при входе в проблему, запись при восстановлении
alert_state() {
  local f="$MONITOR_DIR/state/alert_$1"
  if [ "$2" = 1 ]; then
    if [ ! -e "$f" ]; then : >"$f"; alert "$3"; fi
  elif [ -e "$f" ]; then
    rm -f "$f"; log info recovered "$1"
    if [ -n "${NOTIFY_RECOVERY:-}" ]; then alert "восстановлено: $1"; fi
  fi
}

# autoheal NAME: перезапуск unhealthy-контейнера, не чаще AUTOHEAL_MAX_PER_HOUR раз в час (защита от цикла)
autoheal() {
  local f="$MONITOR_DIR/state/autoheal_$1" now recent
  now=$(date +%s)
  recent="$(awk -v n="$now" '$1 > n - 3600' "$f" 2>/dev/null)"
  if [ "$(printf '%s' "$recent" | grep -c .)" -ge "${AUTOHEAL_MAX_PER_HOUR:-3}" ]; then
    log warn autoheal_limit "$1: лимит перезапусков в час исчерпан, нужен человек"; return 0
  fi
  if docker restart "$1" >/dev/null 2>&1; then
    { [ -n "$recent" ] && echo "$recent"; echo "$now"; } >"$f"
    log warn autoheal_restart "$1"
  else
    log error autoheal_failed "$1"
  fi
}

# 1. /health: HTTP 200 и "status":"ok"
t0=$(date +%s%3N)
resp="$(curl -ksS -m 5 -w '\n%{http_code}' "$HEALTH_URL" 2>/dev/null || true)"
code="${resp##*$'\n'}"; body="${resp%$'\n'*}"
ms=$(( $(date +%s%3N) - t0 ))
ok=false
if [ "$code" = 200 ] && printf '%s' "$body" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'; then ok=true; fi
printf '{"ts":"%s","check":"health","ok":%s,"http":"%s","ms":%s}\n' \
  "$(date +%Y-%m-%dT%H:%M:%S%z)" "$ok" "${code:-000}" "$ms" >>"$MONITOR_DIR/health-$(date +%Y%m).jsonl"

cnt_f="$MONITOR_DIR/state/fail_count"; n="$(cat "$cnt_f" 2>/dev/null || echo 0)"
if [ "$ok" = true ]; then n=0; else n=$((n + 1)); fi
echo "$n" >"$cnt_f"
if [ "$n" -ge "$THRESH" ]; then alert_state health 1 "/health недоступен $n мин подряд (HTTP ${code:-000})"
elif [ "$n" -eq 0 ]; then alert_state health 0 ""; fi

# 2. контейнеры проекта: упавшие или unhealthy (ldap-test - только для разработки)
if command -v docker >/dev/null 2>&1; then
  bad=""
  while IFS='|' read -r name status; do
    [ -z "$name" ] && continue
    case "$name" in *ldap-test*) continue ;; esac
    case "$status" in
      *unhealthy*)
        bad="$bad $name"
        if [ "${AUTOHEAL:-0}" = 1 ]; then autoheal "$name"; fi ;;
      Up*) ;;
      *) bad="$bad $name" ;;
    esac
  done < <(docker ps -a --filter "label=com.docker.compose.project=$PROJECT" --format '{{.Names}}|{{.Status}}' 2>/dev/null)
  if [ -n "$bad" ]; then alert_state containers 1 "контейнеры не в порядке:$bad"; else alert_state containers 0 ""; fi
fi

# 3. свежесть бэкапа
if [ -d "$BACKUP_DIR" ]; then
  last="$(cat "$BACKUP_DIR/last_success" 2>/dev/null || echo 0)"
  age_h=$(( ($(date +%s) - last) / 3600 ))
  if [ "$age_h" -gt "$BACKUP_MAX_AGE_H" ]; then alert_state backup 1 "последний успешный бэкап старше ${BACKUP_MAX_AGE_H} ч"; else alert_state backup 0 ""; fi
fi

# 4. свежесть внешней копии (только если она включена)
if [ -n "${OFFSITE_RCLONE_REMOTE:-}" ] && [ -d "$BACKUP_DIR" ]; then
  last="$(cat "$BACKUP_DIR/offsite_last_success" 2>/dev/null || echo 0)"
  age_h=$(( ($(date +%s) - last) / 3600 ))
  if [ "$age_h" -gt "${OFFSITE_MAX_AGE_H:-48}" ]; then alert_state offsite 1 "последняя успешная выгрузка в облако старше ${OFFSITE_MAX_AGE_H:-48} ч"; else alert_state offsite 0 ""; fi
fi
exit 0
