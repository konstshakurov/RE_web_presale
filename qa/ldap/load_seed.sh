#!/usr/bin/env bash
# Одноразовый тестовый LDAP для KON-56 и загрузка seed.ldif. Профиль compose не трогает:
# отдельный контейнер на 127.0.0.1:${QA_LDAP_PORT:-2389}, том не создаётся (данные исчезают с контейнером).
# Нужны переменные (из локального .env, не из репозитория):
#   QA_LDAP_ADMIN_PW, QA_LDAP_PW_ADMIN, QA_LDAP_PW_USER, QA_LDAP_PW_OUTSIDER, QA_LDAP_PW_REVOKED
# Использование:  set -a; . ./.env; set +a; bash qa/ldap/load_seed.sh up|down
set -euo pipefail
NAME=re_qa_ldap
PORT=${QA_LDAP_PORT:-2389}
HERE=$(cd "$(dirname "$0")" && pwd)
BASE="dc=qa,dc=example,dc=test"
need() { [ -n "${!1:-}" ] || { echo "нет переменной $1 (задайте в локальном .env)"; exit 2; }; }

case "${1:-up}" in
  down) docker rm -f "$NAME" >/dev/null 2>&1 && echo "контейнер $NAME удалён"; exit 0 ;;
  up) ;;
  *) echo "использование: $0 up|down"; exit 2 ;;
esac
for v in QA_LDAP_ADMIN_PW QA_LDAP_PW_ADMIN QA_LDAP_PW_USER QA_LDAP_PW_OUTSIDER QA_LDAP_PW_REVOKED; do need $v; done

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -p "127.0.0.1:$PORT:389" \
  -e LDAP_ORGANISATION="QA Fiction" -e LDAP_DOMAIN=qa.example.test \
  -e LDAP_ADMIN_PASSWORD="$QA_LDAP_ADMIN_PW" -e LDAP_TLS=false \
  osixia/openldap:1.5.0 >/dev/null
for i in $(seq 1 30); do
  docker exec "$NAME" ldapsearch -x -H ldap://localhost -b "$BASE" -D "cn=admin,$BASE" \
    -w "$QA_LDAP_ADMIN_PW" -s base dn >/dev/null 2>&1 && break
  sleep 2
done
sed -e "s|FAKE_PW_ADMIN|$QA_LDAP_PW_ADMIN|" -e "s|FAKE_PW_USER|$QA_LDAP_PW_USER|" \
    -e "s|FAKE_PW_OUTSIDER|$QA_LDAP_PW_OUTSIDER|" -e "s|FAKE_PW_REVOKED|$QA_LDAP_PW_REVOKED|" \
    "$HERE/seed.ldif" | docker exec -i "$NAME" ldapadd -x -H ldap://localhost \
    -D "cn=admin,$BASE" -w "$QA_LDAP_ADMIN_PW" >/dev/null
echo "готово: ldap://127.0.0.1:$PORT/, шаблон bind: uid={login},cn=users,cn=accounts,$BASE"
