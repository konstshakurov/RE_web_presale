# QA-стенд (KON-57/58)

Отдельный compose-проект `re_web_presale_qa`: не затрагивает рабочий стенд разработчика (порты 18080, 1390, 15432).

```
bash qa/stand/stand.sh up      # генерирует qa/stand/.qa.env (вне Git), поднимает стек, грузит seed LDAP
bash qa/stand/stand.sh logs    # логи api/worker/proxy
bash qa/stand/stand.sh down    # снять стенд и тома
```

Состав поверх `docker-compose.yml` и `docker-compose.migrations.yml`: тестовый LDAP (`ldap-test`, `dc=qa,dc=example,dc=test`),
фейк Jira `fake_jira.py` (поведение зависит от логина: `qa-jirablock` - отказ входа до ввода особого пароля, прочие - успех),
порт БД на loopback для проверок NFR-006/007. Пароли и ключи генерируются локально; в репозитории только плейсхолдеры.
После пересоздания `api` перезапустите `proxy` (nginx держит старый IP).

Тесты: `cd backend && python -m pytest tests/qa -q`; e2e: `cd qa/e2e && npm install && npx playwright install chromium && npx playwright test`.
