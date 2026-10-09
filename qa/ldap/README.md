# Тестовый LDAP для QA (KON-56)

Вымышленный каталог `dc=qa,dc=example,dc=test`, раскладка как у FreeIPA:
`uid=<login>,cn=users,cn=accounts,dc=qa,dc=example,dc=test`. Это отдельный одноразовый контейнер
`re_qa_ldap` на `127.0.0.1:2389` (без тома: данные исчезают вместе с контейнером). Профиль
`ldap-test` из `docker-compose.yml` и его данные не затрагиваются; compose не правится.

| Логин | Назначение |
|-------|------------|
| `qa-admin` | верный LDAP; в тестах заведён как admin |
| `qa-user` | верный LDAP; user; есть `cn` и `mail` |
| `qa-outsider` | верный LDAP, НЕ в списке допущенных |
| `qa-revoked` | верный LDAP; в списке, затем отзывается (UC-006 1а); нет `mail` |

## Пароли

В репозитории паролей нет: в `seed.ldif` стоят плейсхолдеры `FAKE_PW_*`. Задайте свои значения в
локальном `.env` (он вне Git), только символы `A-Za-z0-9_-`:

```
QA_LDAP_ADMIN_PW=...      # пароль cn=admin контейнера
QA_LDAP_PW_ADMIN=...
QA_LDAP_PW_USER=...
QA_LDAP_PW_OUTSIDER=...
QA_LDAP_PW_REVOKED=...
```

Сгенерировать: `python -c "import secrets; print(secrets.token_urlsafe(12))"`.

## Загрузка на чистом стенде

```
set -a; . ./.env; set +a
bash qa/ldap/load_seed.sh up      # поднимает контейнер и загружает seed.ldif
```

Нужен Docker и образ `osixia/openldap:1.5.0` (тот же, что в compose). Остановить и удалить:
`bash qa/ldap/load_seed.sh down`.

## Запуск тестов

```
cd backend
python -m pytest tests/qa -q            # без QA_LDAP_PW_* интеграционные тесты пропускаются
```

Для приложения в compose с этим каталогом (ручной вход): `LDAP_URL=ldap://host.docker.internal:2389/`,
`LDAP_BIND_TEMPLATE=uid={login},cn=users,cn=accounts,dc=qa,dc=example,dc=test`; в таблицу допущенных
завести `qa-admin` (admin) через `ADMIN_LOGINS`, остальных - через раздел «Настройки».

## Проверка NFR-006 после ручного входа (канарейка)

```
export NFR006_CANARIES="$QA_LDAP_PW_USER"
python qa/nfr006_check.py --logs-compose
python qa/nfr006_check.py --db-url "$APP_DB_URL"
python qa/nfr006_check.py --repo
python qa/nfr006_check.py --selftest     # негативная проба: должен сообщить, что нарушения на грязных данных найдены
```
