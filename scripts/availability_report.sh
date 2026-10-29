#!/usr/bin/env bash
# Месячный отчёт доступности 9:00-18:00 по будням (KON-49, NFR-003, A7).
# Использование: availability_report.sh [YYYY-MM]  (по умолчанию - прошлый месяц)
# Окружение: MONITOR_DIR, REPORT_DIR, REPORT_CMD (команда, получает Markdown на stdin).
set -euo pipefail
here="$(dirname "$0")"
MONITOR_DIR="${MONITOR_DIR:-/var/lib/re_web_presale/monitor}"
REPORT_DIR="${REPORT_DIR:-$MONITOR_DIR/reports}"
month="${1:-$(date -d "$(date +%Y-%m-01) -1 day" +%Y-%m)}"
mkdir -p "$REPORT_DIR"
out="$REPORT_DIR/availability-$month.md"
python3 -I "$here/availability_report.py" --month "$month" --log "$MONITOR_DIR/health-${month/-/}.jsonl" >"$out"
cat "$out"
if [ -n "${REPORT_CMD:-}" ]; then bash -c "$REPORT_CMD" <"$out"; fi
