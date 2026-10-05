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

Обмен логина и пароля на сессию (`POST /rest/auth/1/session`) реализован
классом `SessionLoginProvider`, но **выключен**: он требует пароль
пользователя, а UC-005 и NFR-006 это запрещают. Класс — документированная
точка расширения, клиент её не вызывает. Попытка включить его без явного
решения владельца поднимает `PermissionError`.

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
`__repr__`, `JiraConfig.safe_summary()` отдаёт только признаки
«задано / не задано», а `client.last_call` содержит путь, код и длительность —
без тела и заголовков. Это проверяется тестами в `tests/test_no_secrets.py`.

## Тесты

```bash
cd src\jira_client
python -m pytest
```

237 тестов, покрытие 97%. Сеть не используется: применяется
`httpx.MockTransport`. Фикстуры обезличены — реальных ключей задач, имён и
адресов в них нет, что проверяется отдельным набором тестов.

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
* Сессия Jira конечна по времени. Восстановление возможно только с паролем
  пользователя — см. «Открытые вопросы» в отчёте.