# Общие функции скриптов бэкапа и мониторинга (KON-49). Подключается через `source`.
# Секреты в скрипты не передаются: БД-команды идут через docker exec внутри контейнера db
# (локальный сокет), вебхук алертов - из env (ALERT_WEBHOOK_URL), в логи не пишется.

# Команда выполнения внутри контейнера db: строка, режется по пробелам.
DB_EXEC="${DB_EXEC:-docker exec -i ${DB_CONTAINER:-re_web_presale-db-1}}"
read -ra EXEC <<<"$DB_EXEC"

json_escape() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | tr '\n\r\t' '   '; }

# log LEVEL EVENT [message] -> одна JSON-строка в stdout (NFR-006: без секретов)
log() {
  printf '{"ts":"%s","level":"%s","component":"%s","event":"%s","msg":"%s"}\n' \
    "$(date +%Y-%m-%dT%H:%M:%S%z)" "$1" "${COMPONENT:-ops}" "$2" "$(json_escape "${3:-}")"
}

# alert MESSAGE: в лог + вебхук/команда, если заданы. Сбой доставки не роняет скрипт.
alert() {
  log error alert "$1"
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
