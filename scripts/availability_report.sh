#!/usr/bin/env bash
# Месячный отчёт доступности 9:00-18:00 по будням без праздников (KON-49, NFR-003, A7).
# Использование: availability_report.sh [YYYY-MM]  (по умолчанию - прошлый месяц)
# Окружение: MONITOR_DIR, REPORT_DIR, HOLIDAYS_FILE (по умолчанию infra/ops/holidays_ru.txt рядом со скриптами),
# SMTP_HOST + ALERT_EMAIL_TO (отчёт письмом; REPORT_EMAIL_TO переопределяет получателя),
# REPORT_CMD (команда, получает Markdown на stdin).
set -euo pipefail
COMPONENT=report
here="$(cd "$(dirname "$0")" && pwd)"
source "$here/ops_common.sh"
MONITOR_DIR="${MONITOR_DIR:-/var/lib/re_web_presale/monitor}"
REPORT_DIR="${REPORT_DIR:-$MONITOR_DIR/reports}"
HOLIDAYS_FILE="${HOLIDAYS_FILE:-$here/../infra/ops/holidays_ru.txt}"
month="${1:-$(date -d "$(date +%Y-%m-01) -1 day" +%Y-%m)}"
mkdir -p "$REPORT_DIR"
out="$REPORT_DIR/availability-$month.md"
python3 -I "$here/availability_report.py" --month "$month" --log "$MONITOR_DIR/health-${month/-/}.jsonl" \
  --holidays "$HOLIDAYS_FILE" >"$out"
cat "$out"
if [ -n "${SMTP_HOST:-}" ]; then
  to="${REPORT_EMAIL_TO:-${ALERT_EMAIL_TO:-}}"
  python3 -I "$here/send_mail.py" --subject "[re_web_presale] Отчёт доступности за $month" ${to:+--to "$to"} --attach "$out" \
    <"$out" || log warn report_delivery_failed "email"
fi
if [ -n "${REPORT_CMD:-}" ]; then bash -c "$REPORT_CMD" <"$out"; fi
