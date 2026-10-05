# Локальное окружение (KON-32)

```
cp .env.example .env            # заполнить блок re_web_presale (значения только локально)
docker compose up --build       # proxy :8080 (127.0.0.1), web, api, worker, db
docker compose --profile ldap-test up --build   # + тестовый LDAP (127.0.0.1:1389)
docker compose -f docker-compose.yml -f docker-compose.migrations.yml up   # + миграции db/migrations (KON-29)
curl http://127.0.0.1:8080/health
bash scripts/check_db_isolation.sh   # cv_rw не видит app_db и наоборот (нужен запущенный db)
bash scripts/check_secrets.sh [--images]
```

Допущения: локально proxy слушает HTTP на loopback (TLS при деплое, KON-49); init БД выполняется
только при первом создании тома `pgdata` (`docker compose down -v` для повторной инициализации);
миграции ожидаются в `db/migrations/app_db` и `db/migrations/cv_db`; тестовый пользователь LDAP
создаётся вручную (ldapadd), пароли в репозитории не хранятся.
