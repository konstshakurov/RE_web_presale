# Общие функции скриптов бэкапа и мониторинга (KON-49). Подключается через `source`.
# Секреты в скрипты не передаются: БД-команды идут через docker exec внутри контейнера db
# (локальный сокет), вебхук алертов и SMTP-пароль - из env (ALERT_WEBHOOK_URL, SMTP_PASSWORD[_FILE]), в логи не пишется.

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Команда выполнения внутри контейнера db: строка, режется по пробелам.
DB_EXEC="${DB_EXEC:-docker exec -i ${DB_CONTAINER:-re_web_presale-db-1}}"
read -ra EXEC <<<"$DB_EXEC"

json_escape() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | tr '\n\r\t' '   '; }

# log LEVEL EVENT [message] -> одна JSON-строка в stdout (NFR-006: без секретов)
log() {
  printf '{"ts":"%s","level":"%s","component":"%s","event":"%s","msg":"%s"}\n' \
    "$(date +%Y-%m-%dT%H:%M:%S%z)" "$1" "${COMPONENT:-ops}" "$2" "$(json_escape "${3:-}")"
}

# send_mail SUBJECT [ATTACH_FILE]: тело - stdin. Почта идёт через scripts/send_mail.py (SMTP_*,
# ALERT_EMAIL_TO из ops.env). Без SMTP_HOST ничего не отправляет. Возвращает 0 при успехе/не настроено.
send_mail() {
  [ -n "${SMTP_HOST:-}" ] || { cat >/dev/null; return 0; }
  local args=(--subject "$1")
  [ -n "${2:-}" ] && args+=(--attach "$2")
  python3 -I "$OPS_DIR/send_mail.py" "${args[@]}"
}

# alert MESSAGE: в лог + почта (SMTP_HOST) + вебхук/команда, если заданы. Сбой доставки не роняет скрипт.
alert() {
  local one
  one="$(printf '%s' "$1" | tr '\n\r' '  ')"
  log error alert "$1"
  if [ -n "${SMTP_HOST:-}" ]; then
    printf '%s\n\nХост: %s\nВремя: %s\nКомпонент: %s\n' "$1" "$(hostname)" "$(date +%Y-%m-%dT%H:%M:%S%z)" "${COMPONENT:-ops}" \
      | send_mail "[re_web_presale] ALERT: $one" >/dev/null \
      || log warn alert_delivery_failed "email"
  fi
  if [ -n "${ALERT_WEBHOOK_URL:-}" ]; then
    curl -fsS -m 10 -X POST -H 'Content-Type: application/json' \
      -d "{\"text\":\"[re_web_presale] $(json_escape "$1")\"}" "$ALERT_WEBHOOK_URL" >/dev/null 2>&1 \
      || log warn alert_delivery_failed "webhook"
  fi
  if [ -n "${ALERT_CMD:-}" ]; then
    printf '%s\n' "[re_web_presale] $1" | bash -c "$ALERT_CMD" >/dev/null 2>&1 \
      || log warn alert_delivery_failed "cmd"
  fi
  return 0
}
