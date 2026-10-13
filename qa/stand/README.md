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

## Дополнения KON-47 (этап 1)

- `fake_jira.py` расширен: поиск (JQL), задачи, эпики Agile, управление сценарием на `127.0.0.1:18090` (`/__qa/*`).
- `fake_llm.py` (сервис `fake-llm`): OpenAI-совместимый фейк с режимами `ok/slow/hang/error500/http401/garbage` и
  журналом тел запросов, `127.0.0.1:18091`. Внутренний провайдер стенда (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` в
  `.qa.env`) смотрит на него.
- `stand.sh up` дописывает недостающие переменные в `.qa.env` (интервалы опроса 20/30 с, порты фейков), поднимает тома
  заново после `down` с применением **всех** миграций (app_db V001..V008, cv_db V001..V010) и проверяет их число
  (`миграции app_db: применено 8 из 8`); при расхождении завершается с ошибкой.
- Прокси стенда собирается с исправлением KON-65 (повторное разрешение адресов `api`/`web` через Docker DNS): после
  `up -d --build` перезапускать `proxy` не нужно.
- Порядок для KON-47: `bash qa/stand/stand.sh down && bash qa/stand/stand.sh up`, затем
  `cd backend && QA_RUN_SLOW=1 python -m pytest tests/qa`; e2e: `cd qa/e2e && npx playwright test`;
  нагрузка: `python tests_load/run_load.py`. Не запускать несколько прогонов одновременно: наборы делят состояние стенда.
