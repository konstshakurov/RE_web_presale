#!/usr/bin/env bash
# QA-стенд KON-57/58: отдельный compose-проект re_web_presale_qa + тестовый LDAP + фейк Jira.
#   bash qa/stand/stand.sh up      сгенерировать qa/stand/.qa.env (вне Git), поднять, загрузить seed
#   bash qa/stand/stand.sh down    остановить и удалить тома проекта
#   bash qa/stand/stand.sh logs    логи api/worker/proxy
# Значения секретов генерируются локально и в репозиторий не попадают (.qa.env в .gitignore).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
ENVF="$HERE/.qa.env"
PROJ=re_web_presale_qa
BASE="dc=qa,dc=example,dc=test"
DC="docker compose -p $PROJ --env-file $ENVF -f $ROOT/docker-compose.yml -f $ROOT/docker-compose.migrations.yml -f $HERE/docker-compose.qa.yml --profile ldap-test"
tok() { python -c "import secrets; print(secrets.token_urlsafe($1))"; }

gen_env() {
  [ -f "$ENVF" ] && return
  local pg app cv key jkey
  pg=$(tok 18); app=$(tok 18); cv=$(tok 18)
  key=$(python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())")
  jkey=$(python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())")
  cat > "$ENVF" <<EOT
POSTGRES_PASSWORD=$pg
APP_RW_PASSWORD=$app
CV_RW_PASSWORD=$cv
APP_DB_URL=postgresql://app_rw:$app@db:5432/app_db
CV_DB_URL=postgresql://cv_rw:$cv@db:5432/cv_db
APP_ENC_KEY=$key
APP_ENC_KEY_ID=k1
JIRA_TOKEN_ENCRYPTION_KEY=$jkey
ADMIN_LOGINS=qa-admin
LDAP_URL=ldap://ldap-test:389/
LDAP_BASE_DN=cn=users,cn=accounts,$BASE
LDAP_BIND_TEMPLATE=uid={login},cn=users,cn=accounts,$BASE
LDAP_USE_TLS=false
SESSION_COOKIE_SECURE=false
SESSION_TTL_HOURS=8
TEST_LDAP_DOMAIN=qa.example.test
TEST_LDAP_ADMIN_PASSWORD=$(tok 12)
JIRA_BASE_URL=http://fake-jira:8080
PROXY_PORT=18080
QA_LDAP_PORT=1390
QA_BASE_URL=http://127.0.0.1:18080
QA_LDAP_PW_ADMIN=$(tok 12)
QA_LDAP_PW_USER=$(tok 12)
QA_LDAP_PW_OUTSIDER=$(tok 12)
QA_LDAP_PW_REVOKED=$(tok 12)
QA_LDAP_PW_JIRABLOCK=$(tok 12)
QA_JIRA_RELOGIN_PW=$(tok 12)
EOT
}

# KON-47: дописать недостающие переменные в существующий .qa.env (значения не перезаписываются)
add_missing() {
  local name="$1" value="$2"
  grep -q "^$name=" "$ENVF" || echo "$name=$value" >> "$ENVF"
}
ensure_env() {
  add_missing QA_DB_PORT 15432
  add_missing QA_JIRA_PORT 18090
  add_missing QA_LLM_PORT 18091
  add_missing QA_FAKE_JIRA_URL http://127.0.0.1:18090
  add_missing QA_FAKE_LLM_URL http://127.0.0.1:18091
  add_missing LLM_BASE_URL http://fake-llm:8080/v1
  add_missing LLM_MODEL fake-qwen
  add_missing LLM_API_KEY "FAKE_internal_$(tok 12)"
  add_missing SYNC_NEEDS_INTERVAL_SEC 20
  add_missing SYNC_EMPLOYEES_INTERVAL_SEC 30
  add_missing SYNC_PAUSE_RECHECK_SEC 5
  add_missing SYNC_OVERLAP_SEC 5
}

# KON-47: все миграции применены (app_db V001..V008, cv_db V001..V010); иначе стенд непригоден
check_migrations() {
  local ok=1 db want have
  for db in app_db cv_db; do
    want=$(ls "$ROOT/db/migrations/$db"/V*.sql | grep -v '\.down\.sql$' | wc -l | tr -d ' 
')
    have=$($DC exec -T db psql -U postgres -d "$db" -Atc "SELECT count(*) FROM meta.schema_migration" | tr -d '
 ')
    echo "миграции $db: применено $have из $want"
    [ "$have" = "$want" ] || ok=0
  done
  [ "$ok" = 1 ] || { echo "ОШИБКА: не все миграции применены (пересоздайте тома: stand.sh down && up)"; return 1; }
}

case "${1:-up}" in
  up)
    gen_env
    ensure_env
    set -a; . "$ENVF"; set +a
    $DC up -d --build
    echo "ожидание LDAP..."
    for i in $(seq 1 40); do
      $DC exec -T ldap-test ldapsearch -x -H ldap://localhost -b "$BASE" -D "cn=admin,$BASE" \
        -w "$TEST_LDAP_ADMIN_PASSWORD" -s base dn >/dev/null 2>&1 && break
      sleep 2
    done
    if $DC exec -T ldap-test ldapsearch -x -H ldap://localhost -b "uid=qa-user,cn=users,cn=accounts,$BASE" \
        -D "cn=admin,$BASE" -w "$TEST_LDAP_ADMIN_PASSWORD" -s base dn >/dev/null 2>&1; then
      echo "seed уже загружен"
    else
      { cat "$ROOT/qa/ldap/seed.ldif"; printf '\n\n'; cat "$HERE/seed_jira.ldif"; } \
        | sed -e "s|FAKE_PW_ADMIN|$QA_LDAP_PW_ADMIN|" -e "s|FAKE_PW_USER|$QA_LDAP_PW_USER|" \
              -e "s|FAKE_PW_OUTSIDER|$QA_LDAP_PW_OUTSIDER|" -e "s|FAKE_PW_REVOKED|$QA_LDAP_PW_REVOKED|" \
              -e "s|FAKE_PW_JIRABLOCK|$QA_LDAP_PW_JIRABLOCK|" \
        | $DC exec -T ldap-test ldapadd -x -H ldap://localhost -D "cn=admin,$BASE" \
            -w "$TEST_LDAP_ADMIN_PASSWORD" >/dev/null
      echo "seed загружен"
    fi
    for i in $(seq 1 40); do
      curl -fsS "http://127.0.0.1:${PROXY_PORT}/health" >/dev/null 2>&1 && {
        check_migrations || exit 1
        echo "стенд готов: $QA_BASE_URL"; exit 0; }
      sleep 3
    done
    echo "стенд не ответил на /health"; exit 1 ;;
  down)
    ensure_env
    set -a; . "$ENVF"; set +a
    $DC down -v ;;
  logs)
    set -a; . "$ENVF"; set +a
    $DC logs --no-color api worker proxy ;;
  *) echo "использование: $0 up|down|logs"; exit 2 ;;
esac
