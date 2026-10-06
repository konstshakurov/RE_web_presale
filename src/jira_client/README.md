# `jira_client` — клиент Jira для re_web_presale

Модуль реализует чтение и запись в **Jira Server 8.13.0** от имени
пользователя **без технической учётной записи и без Application Link**.
Обоснование, ограничения и открытые вопросы — в
[`architecture/probes/KON-30_jira_probe_report.md`](../../architecture/probes/KON-30_jira_probe_report.md).

Модуль реализует только то, что подтверждено пробой. Неподтверждённое
помечено в коде комментарием и в отчёте.

---

## Установка и настройка

Зависимость одна — `httpx` (для криптографии — `cryptography`, она нужна
только для `TokenVault`).

```bash
copy src\jira_client\.env.example .env      # затем заполнить значения
```

Шаблон **не содержит секретов**: все чувствительные переменные
(`JIRA_SESSION_TOKEN`, `JIRA_USERNAME`, `JIRA_PASSWORD`,
`JIRA_TOKEN_ENCRYPTION_KEY`) пустые. Файл `.env` уже исключён из git.

Ключ шифрования генерируется один раз и уходит в менеджер секретов:

```bash
python -c "import sys; sys.path.insert(0,'src'); from jira_client import generate_key; print(generate_key())"
```

## Использование

```python
from jira_client import JiraClient

with JiraClient() as client:                     # конфиг из переменных окружения
    print(client.server_info().version)           # 8.13.0

    # Чтение: только нужные поля (FR-028)
    demand = client.get_demand("ANALYTICS-1234")
    print(demand.summary, demand.status, demand.rate)

    # Правила клиента — только заполненные (FR-015)
    for field_id, value, name in demand.rules:
        print(name, "=", value)

    # Инкрементальный опрос раз в 5 минут (FR-028)
    for issue in client.search_updated_since("2026-10-05 09:00"):
        ...

    # Дети эпика — Agile API, не JQL с `parent`
    for issue in client.iter_epic_children("SA-1234"):
        ...

    # Сам эпик — тот же Agile API; список полей настраивается
    epic = client.get_epic("SA-1234", fields=["summary", "description"])
```

### Запись

Запись всегда идёт от имени пользователя, чей токен передан клиенту, и
перед отправкой перечитывает доступные переходы (FR-028).

```python
# Комментарий (FR-008)
client.add_comment("ANALYTICS-1234", "Назначен кандидат")

# Ставка и исполнитель (FR-018, FR-009)
client.set_fields("ANALYTICS-1234", {"customfield_20811": "1800"})
client.assign("ANALYTICS-1234", "username")

# Вложение CV (FR-008)
client.add_attachment("ANALYTICS-1234", filename="cv.pdf", content=data,
                      mime_type="application/pdf")

# Смена статуса (FR-026): переход ищется по имени целевого статуса,
# обязательные поля проверяются ДО вызова Jira.
transition = client.find_transition("ANALYTICS-1234", to_status="Кандидатов нет")
print(transition.describe())     # «31 Кандидатов нет -> … [обязательно: …]»
client.transition("ANALYTICS-1234", transition.id,
                  fields={"customfield_20904": {"value": "требуется очное присутствие"}})
```

## Поведение при отказах (FR-029)

Тело отказа Jira показывается как есть. Исключения:

| Исключение | Когда |
|---|---|
| `JiraAuthError` | 401, сессии нет или она истекла → нужна повторная авторизация |
| `JiraLoginError` | отказ `POST /rest/auth/1/session`: `login_reason` = `auth_failed` либо `login_blocked` (см. «Вход по паролю») |
| `JiraPermissionError` | 401 с текстом «Вы не авторизованы…» либо 403 — прав не хватает |
| `JiraNotFoundError` | 404 |
| `JiraBadRequestError` | 400, в том числе ошибки JQL; `.field_errors` содержит ошибки полей |
| `JiraConflictError` | 409 |
| `JiraRateLimitedError` | 429 и 503 — единственный код, где повтор оправдан |
| `JiraServerError` | прочие 5xx |
| `JiraTimeoutError` | превышены 10 с (NFR-009) |
| `JiraUnavailableError` | сеть или TLS недоступны |

Jira Server 8.13.0 отдаёт «сессия истекла» и «нет прав» **одним кодом 401**,
различая их только текстом тела. Клиент различает их сам, иначе FR-029
показывал бы пользователю неверную причину.

**Повторы выключены по умолчанию.** NFR-009 даёт 10 с на вызов; повтор внутри
этого лимита означал бы его нарушение. Решение о повторе принимает вызывающий
код. Признак `JiraError.retryable` подсказывает, оправдан ли повтор вообще.

## Аутентификация

| Режим | Что это | Когда применять |
|---|---|---|
| `cookie` | `Cookie: JSESSIONID=<токен>` | **основной.** Токен, а не пароль: шифруется и хранится (NFR-006) |
| `basic` | `Authorization: Basic` | только диагностическая проба или техническая учётка |

### Вход по паролю

Обмен логина и пароля на сессию (`POST /rest/auth/1/session`) выполняет
`SessionLoginProvider`. Включён решением владельца 05.10 в рамках ADR-005:
логин и пароль Jira совпадают с LDAP, поэтому один ввод пароля при входе в
приложение создаёт и сессию Jira.

```python
from jira_client import SessionLoginProvider

provider = SessionLoginProvider("https://jira.simbirsoft.com")
session = provider.login(username, password)   # POST /rest/auth/1/session

# session.cookie_name == "JSESSIONID", session.created_at — время входа (UTC)
client = JiraClient(auth=build_auth(
    auth_mode="cookie",
    session_token=session.token,
    session_cookie=session.cookie_name,
))
```

**Пароль не сохраняется.** Он существует только как тело одного запроса:
не пишется в атрибуты объекта, не попадает в `repr`, в `last_call`, в
исключения и в трассировку, удаляется сразу после отправки. Хранится только
сессионный токен, и приложение держит его зашифрованным (`TokenVault`,
NFR-006).

Тело запроса — **плоское** `{"username", "password"}`. Вложенная форма
`{"session": {...}}` Jira 8.13.0 отклоняет: 400 «Unrecognized field
`session` (Class `com.atlassian.jira.rest.auth.AuthParams`)».

Требуется только непустые логин и пароль; `basic`-режим для входа не нужен.

### Классификация отказов входа

Отказы входа — отдельный набор причин, не сводимый к «неверный пароль»:

| Признак | Признак в ответе Jira | Что делать |
|---|---|---|
| `auth_failed` | 401, `x-seraph-loginreason: AUTHENTICATED_FAILED` | показать форму заново |
| `login_blocked` | 401, `x-seraph-loginreason: AUTHENTICATION_DENIED` | объяснить про CAPTCHA/вход в браузере; повтор по REST не поможет |
| `jira_unavailable` | сеть, TLS, таймаут, 5xx | повторить позже; пароль **не** переспрашивать |

Все три поднимаются `JiraLoginError.login_reason` (кроме `jira_unavailable` —
это `JiraUnavailableError` / `JiraTimeoutError`) и переводятся адаптером в
единую карту `backend/app/integrations/jira/errors.py`.

HTML-страницу, которую Jira отдаёт вместо JSON, показывать нельзя: тело
сжимается до одной строки без разметки, а к отказу входа добавляется
короткий текст `JiraLoginError.hint`.

При отказе входа тело **запроса** (в нём пароль) в исключение не попадает —
`summary` и `body_head` берутся только из ответа Jira.

## Хранение секретов (NFR-006)

```python
from jira_client import TokenVault

vault = TokenVault.from_env()          # ключ из JIRA_TOKEN_ENCRYPTION_KEY
sealed = vault.seal(token)             # AES-256-GCM, nonce + ciphertext, base64
assert vault.open(sealed) == token
```

`TokenVault.seal` использует случайный nonce на каждое шифрование, так что
одинаковые токены не сравнимы между собой. `open` не показывает значение
при ошибке.

Секреты не попадают в логи: у стратегий аутентификации переопределён
`__repr__`, у `JiraConfig` и `SessionToken` — тоже (иначе dataclass показал бы
`session_token` и `password` целиком), `JiraConfig.safe_summary()` отдаёт
только признаки «задано / не задано», а `client.last_call` содержит путь, код
и длительность — без тела и заголовков. Это проверяется тестами в
`tests/test_no_secrets.py`, включая сценарий входа по паролю.

## Тесты

```bash
cd src\jira_client
python -m pytest
```

308 тестов, покрытие 97%. Сеть не используется: применяется
`httpx.MockTransport`. Фикстуры обезличены — реальных ключей задач, имён и
адресов в них нет, что проверяется отдельным набором тестов.

Интеграционные тесты адаптера (порт `JiraPort`, карта ошибок, `TokenVault`)
живут рядом с ним и запускаются отдельно:

```bash
cd backend
python -m pytest                      # 40 тестов
```

## Границы модуля

* `priority` и `parent` Jira **не отдаёт**. Ключей в ответе задачи нет, а не
  `null`, поэтому читать их нужно через `.get()` (FR-028).
* Граф workflow целиком недоступен через REST: переходы видны только из
  текущего статуса задачи. FR-026 это учитывает.
* `GET /rest/api/2/issue/{key}/attachment` на 8.13.0 отдаёт 404, поэтому
  вложения читаются из поля задачи, а `editmeta` покрывает лишь 39–50 полей
  и не является источником `allowedValues`.
* CORS на экземпляре не включён: браузер не может ходить в REST Jira
  напрямую, нужен серверный посредник.
* Сессия Jira конечна по времени. Восстановление — повторный ввод того же
  пароля через `SessionLoginProvider.login()` (ADR-005); измеренное время
  жизни сессии — в [`architecture/probes/KON-34_report.md`](../../architecture/probes/KON-34_report.md).
* Поле эпика — `customfield_10101` (`Epic Link`) в паре с Agile API.
  Альтернатива `customfield_11032` остаётся открытым вопросом владельца
  процесса; выбор фиксируется `JiraClient` и адаптером (`epic_link_field`).