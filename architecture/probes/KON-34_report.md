# KON-34 — доработка `jira_client`, порт `JiraPort` и адаптер для приложения (Dev-18)

| Атрибут | Значение |
|---------|----------|
| Задача | KON-34 «Pre.Dev-3: jira_client: доработка, чтение и вход по паролю» (Linear, срок 2026-10-12) |
| Зависимые | Pre.Dev-18 (KON-52, сессия Jira при входе), Pre.Dev-4 (KON-35, синхронизация) |
| Дата | 2026-10-06 |
| Экземпляр | `https://jira.simbirsoft.com`, Jira Server **8.13.0**, build 813000 |
| Основание | ADR-005 (`architecture/03_adr.md`), решение владельца от 05.10 — снять запрет на вход по паролю |
| Область записи | `src/jira_client/`, `backend/app/integrations/jira/`, `backend/tests/`, этот файл |
| Тесты | **348 зелёных без сети** (308 + 40), см. раздел 2 |
| Живая проверка | **выполнена 06.10**: `login` + `check_session` + чтение `ANALYTICS-5871` (ключ владельца), `search_incremental` после фикса JQL, `get_epic`/`get_epic_children`; баг JQL найден и исправлен (раздел 4) |
| Время жизни сессии Jira | **замерено**: простой ≈300 с (5 мин), при опросе раз в 120 с — живёт ≥30 мин (раздел 5) |

---

## 1. Что сделано

### 1.1 Вход по паролю включён (`п. 1`)

`SessionLoginProvider` перестал быть заглушкой: флаг `enabled` и
`PermissionError` удалены, вместо них рабочая операция.

| Было | Стало |
|---|---|
| `enabled: bool`, по умолчанию `False` | атрибута нет вовсе |
| `login(...)` при `enabled=False` → `PermissionError` | `login(username, password) -> SessionToken` |
| тело не отправлялось | один `POST /rest/auth/1/session`, **плоское** тело `{"username", "password"}` |
| пароль копировался в атрибуты для «отложенной» отправки | тело живёт в локальном словаре и удаляется в `finally` сразу после отправки |

Возвращаемый `SessionToken` — frozen-dataclass с `repr=False`: имя cookie,
значение токена, `created_at` (UTC). Собственный `__repr__` печатает только
`cookie_name` и время, токена в нём нет.

Форма тела не изменена: плоский объект подтверждён пробой KON-30 (раздел
отчёта про `POST /rest/auth/1/session`); вложенная форма
`{"session": {...}}` Jira 8.13.0 отклоняет — 400 «Unrecognized field
`session`».

**Пароль не сохраняется.** Он существует ровно как тело одного HTTP-запроса:
не пишется в атрибуты провайдера, не попадает в `repr`, в `last_call`, в
исключения и в трассировку. Хранится только сессионный токен, и приложение
держит его исключительно зашифрованным (п. 5).

Обновлены `src/jira_client/README.md` (новый раздел «Вход по паролю» и
«Классификация отказов входа», убраны «выключен» и «запрещено UC-005 и
NFR-006») и `src/jira_client/.env.example` (поля `JIRA_USERNAME` /
`JIRA_PASSWORD` больше не описаны как запрещённые).

### 1.2 Пароль и токен не утекают (`п. 2`, критерий 4)

Что именно закрыто:

| Место утечки | Мера |
|---|---|
| `repr` провайдера, токена, конфигурации | `__repr__` переопределён; `JiraConfig` — `@dataclass(repr=False)` + `safe_summary()` |
| `last_call` клиента | содержит только путь, код и длительность — без тела и заголовков |
| `JiraError` при отказе входа | `summary` и `body_head` берутся **только из ответа Jira**; тело запроса (в нём пароль) в исключение не попадает |
| HTML-страница Jira | тело сжимается до одной строки без разметки, пользователю отдаётся короткий `JiraLoginError.hint` |
| логи адаптера | `SealedSession.describe()`, `JiraAdapter.describe()`, `JiraIntegrationError.describe()` — ни токена, ни пароля, ни cookie |
| фикстуры | только синтетические значения (`ANALYTICS-1`, `SA-1`, `probe-user`, `temporary-login-password`) |

Тесты: `src/jira_client/tests/test_no_secrets.py` (класс `TestLoginDoesNotLeak`)
и `src/jira_client/tests/test_login.py` (класс на утечку пароля), плюс
`backend/tests/test_jira_integration.py::TestNoSecrets`.

### 1.3 Отказы входа классифицируются отдельно (`п. 3`, критерий 1)

В `src/jira_client/errors.py` добавлены
`LOGIN_REASON_AUTH_FAILED` / `LOGIN_REASON_BLOCKED` / `LOGIN_REASON_UNAVAILABLE`,
константы заголовка `x-seraph-loginreason`, функция
`classify_login_failure(response)` и исключение `JiraLoginError`
(наследник `JiraAuthError`) с полями `login_reason` и `hint`.

| Признак | Как опознаётся | Что видит приложение |
|---|---|---|
| `auth_failed` | 401 + `x-seraph-loginreason: AUTHENTICATED_FAILED` | неверный логин или пароль — показать форму заново |
| `login_blocked` | 401 + `x-seraph-loginreason: AUTHENTICATION_DENIED` | CAPTCHA / блокировка; объяснить, что нужен вход через браузер, повтор по REST не поможет |
| `jira_unavailable` | сеть, TLS, таймаут, 5xx, rate limit | повторить позже; **пароль не переспрашивать** |

`auth_failed` и `login_blocked` поднимаются `JiraLoginError`; недоступность —
`JiraTimeoutError` / `JiraUnavailableError`, то есть это **не** «неверный
пароль» и не ошибка классификации. HTML-ответ пользователю не отдаётся.

### 1.4 Порт `JiraPort` (`п. 4`, критерии 2–3)

`backend/app/integrations/jira/port.py` — `Protocol`, который знает только
приложение:

| Операция | Эндпоинт | Примечание |
|---|---|---|
| `login(username, password)` | `POST /rest/auth/1/session` | → `SealedSession` (запечатанный токен + `created_at`) |
| `check_session(sealed_token)` | `GET /rest/api/2/myself` | → `ok \| needs_reauthorization \| jira_unavailable` (+ `no_permission`); **не бросает** |
| `search_incremental(sealed_token, since, …)` | `GET /rest/api/2/search` | JQL по `updated`, сортировка `updated ASC` — возвращает **только изменённые** |
| `get_issue(sealed_token, key, …)` | `GET /rest/api/2/issue/{key}` | список полей настраивается |
| `get_epic(sealed_token, key, …)` | `GET /rest/agile/1.0/epic/{key}` | добавлен в `src/jira_client/client.py` |
| `get_epic_children(sealed_token, key, …)` | `GET /rest/agile/1.0/epic/{key}/issue` | **не** JQL `parent` |

Поле эпика — `customfield_10101` (`Epic Link`), константа
`JiraAdapter.epic_link_field` и `jira_client.fields.EPIC_LINK`. JQL с
`parent = X` не используется вообще: проба KON-30 показала 0 результатов
против 282 у Agile API и `"Epic Link" = X`.

Списки полей настраиваются двумя способами: свойством адаптера
(`read_fields`, `epic_fields`) и аргументом конкретного вызова
(`fields=[...]`), аргумент вызова имеет приоритет.

### 1.5 `TokenVault` подключён (`п. 5`)

`JiraAdapter.login()` возвращает **только** `sealed_token` —
`base64(nonce || ciphertext || tag)` от `TokenVault` (AES-256-GCM, ключ из
env `JIRA_TOKEN_ENCRYPTION_KEY`). Открытый токен существует внутри адаптера
единственную секунду между `vault.open()` и постановкой cookie в запрос и
наружу не возвращается никому.

`check_session("")` → `needs_reauthorization` / `no_credentials`;
битый токен или другой ключ шифрования → `CryptoError` →
`needs_reauthorization` (сессия утрачена, нужен вход), а не падение.

### 1.6 Адаптер (`п. 6`)

`backend/app/integrations/jira/adapter.py` — тонкий слой: 6 публичных
операций, каждый вызов = один `JiraClient` на одну операцию поверх
переиспользуемого `httpx.Client`. Приложение импортирует только
`app.integrations.jira` и не знает про `httpx`, cookie и коды HTTP.

**Единая карта ошибок** (`errors.py`). Наружу выходит один класс
`JiraIntegrationError` с единственным признаком `code`:

| `code` | Откуда | Что делать |
|---|---|---|
| `needs_reauthorization` | 401 `login_required` / `invalid_cookie_or_expired` / `auth_failed` при чтении; битый sealed-токен; нет cookie | спросить пароль заново |
| `no_permission` | 401 «Вы не авторизованы…», 403 | показать отказ; повторный вход не поможет |
| `auth_failed` | отказ `POST /rest/auth/1/session` (`AUTHENTICATED_FAILED`) | показать форму заново |
| `login_blocked` | `AUTHENTICATION_DENIED` | объяснить про браузер/CAPTCHA |
| `jira_unavailable` | сеть, TLS, таймаут, 5xx, rate limit | повторить позже, пароль не спрашивать |
| `jira_error` | 400/404/409 и прочее (остаточный) | показать сообщение Jira как есть |

`classify()` вызывается в трёх местах: при входе, в `_client()` (все операции
чтения) и при распечатывании токена. `JiraPort` не выдаёт ни одного класса
`jira_client` и ни одного `httpx`-исключения — это проверено отдельным тестом
`test_no_httpx_exception_escapes_the_port`.

«Jira недоступна» и «сессия истекла» различаются во всех трёх точках
(критерий 6): `SessionCheck.state` для `check_session`, `code` + свойства
`.needs_reauthorization` / `.jira_unavailable` для остального.

`check_session` возвращает **четыре** состояния, а не три: контракт Dev-18
(`ok`, `needs_reauthorization`, `jira_unavailable`) плюс `no_permission`,
который ADR-005 требует различать — «нет прав» не лечится ни повторным
входом, ни ожиданием. `SessionCheck` не бросает исключений: результат нужен
как логическое значение.

### 1.7 Классификация 401 сохранена (`п. 7`)

Значения `reason` не менялись и остаются:
`no_credentials`, `login_required`, `no_permission`, `auth_failed`,
`invalid_cookie_or_expired` (+ `unknown` для неизвестных).
Проверяется `src/jira_client/tests/test_errors.py` и
`TestCheckSession::*` адаптера.

### 1.8 Повторы и лимит времени (критерий 5)

`JiraConfig` по умолчанию: `timeout=10.0` с на вызов (NFR-009),
ретраи **выключены**. Адаптер создаёт `httpx.Client` с
`follow_redirects=False` и без политики повторов.
### 1.9 Баг, найденный живой пробой: JQL `AND ORDER BY` (исправлен)

Живая проверка раздела 4 поймала **400** у `search_incremental`, который 308
тестов не видели: `search_updated_since` собирал JQL через `" AND ".join(parts)`,
включая кусок `"ORDER BY updated ASC, key ASC"` — получалось `… AND ORDER BY …`.
Тест проверял подстроки и пропускал сломанную строку.

Исправление — WHERE и `ORDER BY` собираются раздельно
(`" AND ".join(conditions) + " ORDER BY …"`), тест `test_jql_shape` переписан
на точное равенство JQL + явную проверку отсутствия `AND ORDER BY`.
Вживую после фикса: `search_incremental` вернул 100 задач, застарелых — 0.
Контракт порта дополнен фактом, что Agile Epic API отдаёт **плоский** объект и
игнорирует `fields`. Подробности — п. 4.3.

---

## 2. Команды → результат

Все **обычные прогоны** — без сети, транспорт `httpx.MockTransport`.
Живые проверки (с сетью, на учётке владельца) — в конце таблицы.

| # | Команда | Результат |
|---|---|---|
| 1 | `cd src/jira_client` → `python -m pytest --tb=no` | **308 passed** in 1.66 s |
| 2 | `cd src/jira_client` → `python -m pytest --cov=jira_client --cov-report=term` | **308 passed**, покрытие **97 %** (`TOTAL 1072 stmts, 34 miss`) |
| 3 | `cd backend` → `python -m pytest --tb=short` | **40 passed** in 0.57 s (39 интеграционных + `test_health`) |
| 4 | `cd backend` → `python -m pytest tests/test_health.py -v` | `test_health PASSED` — существующий тест не сломан |
| 5 | `python -m pytest tests -q --tb=line` (корень репо) | **2989 passed**, 2 failed — см. ниже |
| 6 | проверка неиспользуемых импортов (AST по 16 изменённым файлам) | чисто, кроме `from __future__ import annotations` (ложное срабатывание) |
| 7 | `git status --short` | 11 изменённых + 8 новых записей; **мои** — см. раздел 7, остальные существовали до KON-34 |
| 8 | **живая** `kon34_live_step1.py` (вход/сессия) | `login` 751 мс → `check_session` ok 22 мс; подделанный и пустой токен → `needs_reauthorization` (п. 4.2) |
| 9 | **живая** `kon34_ttl_phases.py` (замер TTL) | простой 240 с и 300 с → `ok`; опрос раз в 120 с × 15 → 15/15 `ok`; раздел 5 |
| 10 | **живая** `kon34_live_read.py` (чтение) | `get_issue(ANALYTICS-5871)` 16/16 полей; `search_incremental` 100 задач (после фикса JQL, п. 4.3); `get_epic` + `get_epic_children` 100 шт. (п. 4.2) |

**Две падающие корневые проверки — предсуществующие и не связаны с задачей:**

```
FAILED tests/test_json_writer.py::TestNoDirectWritesRemain::test_no_skill_module_dumps_json_into_a_file_itself
FAILED tests/test_project_id_validation.py::TestEveryToolTakingAProjectIdIsGuarded::test_all_of_them_carry_the_boundary_decorator
```

Обе падали до KON-34: они проверяют обёртки `skills.analyst_*` MCP-инструментов
(`@guard_artifact_errors`, запрет прямой записи JSON), последний коммит этих
файлов — `6d57d66 Initial commit`. В `tests/` нет ни одного упоминания
`jira_client`, `JiraAdapter` или `integrations.jira`, изменённых файлов
`tests/` в `git status` нет.

### Разбивка тестов

`src/jira_client` (308):

| Файл | Тестов |
|---|---:|
| `tests/test_models.py` | 46 |
| `tests/test_client_read.py` | 70 |
| `tests/test_client_write.py` | 31 |
| `tests/test_errors.py` | 36 |
| `tests/test_fields.py` | 27 |
| `tests/test_login.py` | 22 (новый) |
| `tests/test_auth.py` | 21 |
| `tests/test_no_secrets.py` | 20 |
| `tests/test_crypto.py` | 18 |
| `tests/test_config.py` | 17 |

`backend` (39 новых + 1 существующий):

| Класс | Тестов | Что закрывает |
|---|---:|---|
| `TestLogin` | 8 | критерий 1 (успех, `auth_failed`, `login_blocked`, `jira_unavailable` × 3), плоское тело, `SealedSession` |
| `TestCheckSession` | 10 | `ok`, `needs_reauthorization`, `jira_unavailable`, `no_permission`, HTML не отдаётся, ровно один запрос |
| `TestSearchIncremental` | 4 | критерий 2: JQL по `updated`, `updated ASC`, только изменённые, настраиваемые поля и фильтры |
| `TestGetIssue` | 2 | настраиваемый список полей |
| `TestEpic` | 4 | критерий 3: Agile API, не JQL `parent`; `customfield_10101` |
| `TestCallLimits` | 3 | критерий 5: 10 с, повторы off, один запрос на чтение |
| `TestUnifiedErrorMap` | 5 | критерий 6: пять операционных кодов, `httpx` не выходит наружу |
| `TestNoSecrets` | 3 | критерий 4: `repr`, `describe`, исключения |
| `test_health` | 1 | существующий, не сломан |

---

## 3. Критерии приёмки

| # | Критерий | Статус | Основание |
|---|---|---|---|
| 1 | `login` → токен → `check_session` даёт `ok`; неверный пароль → `auth_failed`; блокировка → `login_blocked`; недоступна → `jira_unavailable`, а не «неверный пароль» | **готово** | `login` → `ok` и `check_session` → `ok` выполнены **фактически** (п. 4.2); три ветки отказов — на `MockTransport` (`TestLogin`, 22 теста), вживую намеренно не прогонялись (п. 4.4) |
| 2 | инкрементальная выборка возвращает только изменённые | **готово** | `TestSearchIncremental::test_only_changed_issues_are_returned`, `::test_jql_shape`; вживую — 100 задач, 0 застарелых (п. 4.2) |
| 3 | дети эпика через Agile API; список полей настраивается | **готово** | `TestEpic` (4), `TestGetIssue` (2); вживую — `get_epic_children` 100 шт., `fields=[summary,status]` вернул только их (п. 4.2) |
| 4 | ни пароль, ни токен, ни cookie в логи, `repr`, исключения, фикстуры | **готово** | `test_no_secrets.py` (20) + `TestNoSecrets` (3) |
| 5 | повторы выключены, лимит 10 с | **готово** | `TestCallLimits` (3) |
| 6 | все тесты зелёные без сети; живая проверка `login` + `check_session` + чтение одной задачи записана в этот отчёт | **готово** | 348 зелёных без сети; вживую: `login`, `check_session`, чтение `ANALYTICS-5871` (ключ владельца) — п. 4.2; разграничение истекла/недоступна — п. 5.3.4 |

---

## 4. Живая проверка — выполнена

### 4.1 Настройка окружения (выполнено)

Поля добавлены в корневой `.env` (блок «Jira: вход по паролю», строки 71–102).
`.env` исключён из git (`.gitignore:9`, подтверждено `git check-ignore -v .env`).

| Поле | Статус | Кто заполнял |
|---|---|---|
| `JIRA_USERNAME` (строка 81) | задано | владелец |
| `JIRA_PASSWORD` (строка 82) | задано | владелец |
| `JIRA_TOKEN_ENCRYPTION_KEY` (строка 92) | сгенерировано | команда ниже, значение на экран не выводилось |
| `JIRA_SESSION_TOKEN` (строка 102) | **пусто, так и должно** | вход идёт через пароль |

Ключ шифрования — 32 случайных байта в base64 (`generate_key()`), заполняет
только пустую строку, повторный запуск идемпотентен:

```bash
python -c "import sys,re; sys.path.insert(0,'src'); from jira_client import generate_key; p='.env'; s=open(p,encoding='utf-8').read(); open(p,'w',encoding='utf-8',newline='').write(re.sub(r'(?m)^JIRA_TOKEN_ENCRYPTION_KEY=\s*$','JIRA_TOKEN_ENCRYPTION_KEY='+generate_key(),s))"
```

Команда проверена заранее на копии `.env`: ключ декодируется ровно в 32 байта,
`TokenVault.seal()`/`open()` проходят. Проверка заданности без вывода значения
(строка 99):

```bash
python -c "import sys; sys.path.insert(0,'src'); import jira_client; jira_client.load_dotenv('.env'); print(jira_client.JiraConfig.from_env().safe_summary()['encryption_key_set'])"
```

Перед запуском верифицировано: `basic_credentials_set=True`,
`encryption_key_set=True`, `session_token_set=False`, `timeout=10.0`,
`verify_tls=True`. Значения в отчёт не выгружались — только признаки.

### 4.2 Выполнено фактически — 2026-10-06

**Этап 1 — вход и сессия.** Скрипт: `login` → `check_session` → негативные
сценарии, вывод без секретов.

| Шаг | Фактический результат | Время |
|---|---|---|
| `adapter.login(...)` | `SealedSession`: `cookie=JSESSIONID`, `sealed_length=80`, `created_at=2026-10-06T10:10:49+00:00` | **751 мс** |
| `repr(SealedSession)` | `<SealedSession cookie=JSESSIONID created_at=2026-10-06T10:10:49+00:00 sealed=***>` — ни токена, ни логина | — |
| `adapter.check_session(sealed)` | `state=ok`, `reason=ok`, `status=200` | **22 мс** |
| повторный `check_session` (кэш соединения) | `state=ok`, `status=200` | 18 мс |
| подделанный sealed-токен | `state=needs_reauthorization`, «Сессионный токен не читается: требуется повторный вход» | — |
| пустой токен | `state=needs_reauthorization`, `reason=no_credentials`, «Сессия не создана» | — |

**Этап 2 — чтение (ключ `ANALYTICS-5871` выдал владелец**; только read-only).
Скрипт: `login` → `check_session` → `get_issue` → настраиваемые поля →
`search_incremental` → `get_epic` → `get_epic_children`.

| Шаг | Фактический результат | Время |
|---|---|---|
| `get_issue("ANALYTICS-5871")` | **16 из 16** запрошенных полей: `summary`, `description`, `status`, `issuetype`, `updated`, `created`, `assignee`, `customfield_10101` (epic link) + 4 rate-поля + 4 demand-правила | **47 мс** |
| ключ / тип / статус / updated | `ANALYTICS-5871` · «Потребность» · статус «В работе» · `updated=2026-10-05T12:52:13+0400` | — |
| `summary` / `description` | заголовок есть (текст в отчёт не привожу) · `description` длина 0 симв. | — |
| `epic_link` | `customfield_10101 = SA-3593` | — |
| `get_issue(..., fields=["summary","status"])` | вернулись **только** эти два поля — список полей настраивается вызовом | — |
| `search_incremental(since="2026-09-06 12:26", max_pages=1)` | **100 задач** за 261 мс, все `updated` внутри окна (застарелых — 0), первые ключи: `ANALYTICS-5687`, `ANALYTICS-2346`, `ANALYTICS-5737`, … | 261 мс |
| `get_epic("SA-3593")` | ok — плоский объект Agile API: `id, key, name, summary, done, color, self`; `summary` и `name` приходят **независимо от** `fields=` | — |
| `get_epic_children("SA-3593", max_pages=1)` | ok — **100 задач** через Agile API (`/rest/agile/1.0/epic/{key}/issue`), первые ключи: `WEB1C-1886`, `BACK-12053`, `SA-3845`, … | — |

Секреты в вывод не попали (логин замаскирован `k***`, пароль — только длиной,
sealed-токен — звёздочками, в журнал — только время и код). Чтение `updated ASC`
по проекту `ANALYTICS` подтвердило контракт инкрементального опроса FR-028
на реальных данных: 0 задач с `updated` раньше окна → потерь при выборке нет.

### 4.3 Найденный и исправленный баг: `AND ORDER BY`

Живая проба п. 4.2 поймала **ошибку 400** у `search_incremental`, которую 308
тестов не видели:

* ответ Jira: «Ошибка в запросе JQL … — символ `ORDER`: ожидался конец строки»;
* причина: `search_updated_since` (`src/jira_client/client.py`) собирал JQL как
  `" AND ".join(["project = …", "updated > …", "ORDER BY updated ASC, key ASC"])`
  — сортировка склеивалась через ` AND `, получалось `… AND ORDER BY …`;
* почему тесты не поймали: `test_jql_shape` проверял **подстроки**
  (`"ORDER BY updated ASC" in jql`), а не весь JQL — подстрока проходила даже в
  сломанной строке;
* фикс: WHERE (`project AND updated > …`) собирается отдельно, `ORDER BY`
  добавляется конкатенацией `" AND ".join(conditions) + " ORDER BY …"`; тест
  переписан на точное равенство JQL + явная проверка «`AND ORDER BY` отсутствует».

Это показательный случай: чтение на живой Jira закрыло дыру, которую ни
фикстуры, ни MockTransport не видели. Баг внёс именно KON-34 в методе
`search_updated_since` (KON-30 таким методом не пользовался и его JQL не
ломается).

### 4.4 Намеренно не выполнялось — и почему

| Шаг | Статус | Причина |
|---|---|---|
| **неверный пароль на живой Jira** | **выполнять намеренно нельзя** | реальный отказ входа может вызвать `AUTHENTICATION_DENIED` и блокировку учётной записи владельца. Ветка `auth_failed` покрыта `TestLogin::test_wrong_password_is_auth_failed` на `MockTransport`; риск на боевом стенде не оправдан |
| сетевой отказ (`jira_unavailable`) | не выполнялся вживую | то же: требует порчи реального подключения. Покрыто тестами `test_unreachable_jira…`, `test_timeout_is_jira_unavailable…` |

**Итог по критерию 6 — выполнено полностью:** `login`, `check_session` и
чтение одной задачи (`ANALYTICS-5871` на ключе владельца) выполнены фактически
и записаны в п. 4.2; разграничение «Jira недоступна» vs «сессия истекла»
подтверждено на реальном ответе (раздел 5.3, п. 4).

---

## 5. Время жизни сессии Jira

**Замерено 06.10.2026 на живой Jira.** Три эксперимента, ~45 минут,
только `POST /rest/auth/1/session` и `GET /rest/api/2/myself`.

### 5.1 Ответ коротко

| Сценарий | Измеренное | Что это значит |
|---|---|---|
| **Простой без запросов** | **≈300 с (5 минут)** от последнего запроса | пауза в приложении дольше 5 минут → пароль заново |
| **Регулярные запросы раз в 120 с** | **живёт ≥30 минут** — 15 из 15 проверок `ok`, возраст сессии вырос до 1801 с | опрос поддерживает сессию; обрыва и деградации не видно |
| допущение «8 ч» (`06_plan_and_tasks.md:76`) | **не подтверждено** | это TTL сессии самого приложения в `app_db`, к Jira отношения не имеет |

**Ответ на вопрос задачи «как часто пользователю вводить пароль повторно»:**
если приложение не обращается к Jira 5 минут — повторный ввод. Если
обращается (воркер опрашивает раз в 120 с) — сессия живёт неограниченно
долго по крайней мере 30 минут подряд.

### 5.2 Протокол

| # | Вход (UTC) | Простой | Проверка | Результат |
|---|---|---|---|---|
| 1 | 10:12:05 | 300 с **без единого запроса после входа** | 10:17:05 | **401** `needs_reauthorization` / `invalid_cookie_or_expired` |
| 2 | 11:11:56 | базовая проверка `ok` → простой 240 с | 11:15:56 (`age=240.1 c`) | **200** `ok` |
| 3 | 11:15:56 | базовая проверка `ok` → простой 300 с | 11:20:56 (`age=300.0 c`) | **200** `ok` |
| 4 | 11:20:56 | опрос каждые 120 с, 15 раз | 11:22:56 … 11:50:57 (`age=120→1801 c`) | **200 `ok` 15 раз из 15** |

Эксперименты 1 и 3 выглядят противоречивыми (300 с → умер, 300 с → жив),
но не противоречат друг другу, если смотреть на момент **последнего**
запроса:

* в №1 после входа не было **ни одного** запроса, а момент проверки
  записан с усечением (`int(elapsed)`), так что фактический простой
  составил ≈300.2–301.0 с — **за** границей;
* в №3 базовая проверка прошла в `age=0.0 c`, и простой от неё составил
  ≈299.95 с — **перед** границей.

Разброс 0.3 с при пороге 300 с. Вывод: **серверный timeout — ровно 300 с
от последнего запроса**, эксперименты лежат по обе стороны порога.
Согласуется со значением 300 с по умолчанию, но **в консоли
администратора Jira не проверялось** — это остаётся открытым вопросом.

Для полноты: эксперимент с простоем 240 с (№2) подтверждает, что ниже
порога сессия жива, то есть обрыв — не случайность и не сетевая ошибка.

### 5.3 Что это меняет для архитектуры

1. **Воркер FR-028 опрашивает раз в 5 минут — ровно на границе.**
   `05_deployment_security.md:48`: «потребности 5 мин». Интервал равен
   таймауту, поэтому первый же опрос с большой вероятностью попадёт за
   границу (как эксперимент №1) и получит 401. **Нужен интервал заметно
   меньше 300 с** — замер при 120 с дал 15/15 `ok` за 30 минут, запас 2×.
   Рекомендация: опрос сессии/потребностей не реже, чем раз в 120 с, либо
   пересоздание сессии перед каждой серией опросов.
2. **Допущение «8 ч» убрать из планирования.** Оно про сессию приложения,
   но читается как про Jira. Факт — 5 минут простоя.
3. **UX: пауза > 5 мин = повторный ввод пароля.** Для фонового опроса без
   активного пользователя сессия не жизнеспособна вовсе — её придётся
   создавать на каждый цикл, что возвращает открытый вопрос KON-30 про
   фоновый опрос и техническую учётку.
4. **Классификация 401 подтверждена вживую.** Ответ Jira —
   `invalid_cookie_or_expired`, код 401 — порт вернул
   `needs_reauthorization`, а **не** `jira_unavailable`. Это ровно то,
   что требует критерий 6 («различать Jira недоступна и сессия истекла»),
   и оно подтверждено на реальном ответе, а не на `MockTransport`.

### 5.4 Что осталось неизвестным

| Вопрос | Почему не измерено |
|---|---|
| предельная длительность при непрерывном опросе | замер шёл 30 минут и остановлен по плану; длиннее смысла нет — сессия стабильна |
| простой строго меньше 240 с | логически живёт (240 с прошло, 300 с — нет); отдельный эксперимент не добавил бы информации |
| точность порога | грань локализована в интервал ≈299.95…300.25 с; для точнее нужен ряд 280/290/295/300/310 с |
| значение в консоли администратора Jira | требует прав администратора экземпляра, мною не запрашивалось |
| поведение после 30 мин активности | не проверялось — см. первую строку |

### 5.5 Журнал

Замеры писались в `%TEMP%\kon34_ttl_active.jsonl` и
`%TEMP%\kon34_ttl_phases.jsonl` (вне репозитория). Формат записи — только
время, состояние и HTTP-код; **ни пароля, ни открытого, ни запечатанного
токена в журнале нет**. Журналы не являются артефактом задачи и в git не
попадают.

---

## 6. Что не удалось / что осталось за рамками

1. **Вживую читалась только одна задача и одна страница выборки** — этого
   требовал критерий 6 и этого достаточно. Полный обход сообщений
   (`search_incremental` до конца, 200 страниц) на живой Jira не гонялся —
   он вернул бы десятки тысяч production-задач без пользы для приёмки.
   Раньше тут стоял блокер «нет тестовых ключей»; снят: ключ `ANALYTICS-5871`
   выдал владелец, чтение выполнено (п. 4.2).
2. **Docker-упаковка `src/jira_client`** — build context у `backend/`
   (`docker-compose*.yml` менять запрещено), поэтому в образ попадает
   только `backend/`, а `import jira_client` в контейнере не разрешится.
   Локально помогает `backend/tests/conftest.py` (bootstrap `sys.path` на
   `../src`), но для деплоя нужен отдельный решение владельца/DevOps:
   либо `pyproject.toml` у `backend/` с path-зависимостью, либо сборка
   `src` в wheel, либо изменение build context. **Не решалось — вне
   разрешённой области записи.**
3. **Корневые тесты `tests/` — 2 предсуществующих падения** (раздел 2),
   не связаны с KON-34 и не правились.
4. **Проверка линтером** — в окружении нет `ruff` / `flake8`
   (`python -m ruff` → `No module named ruff`). Вместо них выполнена
   AST-проверка неиспользуемых импортов и прогон компиляции; PEP-8 по
   ручной сверке.

---

## 7. Изменённые файлы

Все — в разрешённой области `src/jira_client/`, `backend/app/integrations/jira/`,
`backend/tests/`, этот отчёт. `db/migrations/`, `docker-compose*.yml`,
`output/`, `backend/pyproject.toml`, Dockerfile и `requirements*` не тронуты.

**Изменено (11):**

```
src/jira_client/__init__.py            экспорт SessionToken, JiraLoginError, LOGIN_REASON_*
src/jira_client/auth.py                SessionToken, рабочий SessionLoginProvider.login
src/jira_client/client.py              get_epic (Agile API); фикс JQL search_updated_since
src/jira_client/config.py              JiraConfig repr=False + safe_summary
src/jira_client/errors.py              LOGIN_REASON_*, classify_login_failure, JiraLoginError
src/jira_client/.env.example           снят запрет на пароль, описано «не сохраняется»
src/jira_client/README.md              разделы «Вход по паролю» / «Классификация отказов входа»
src/jira_client/tests/test_auth.py     TestSessionLoginProvider без enabled, TestSessionToken
src/jira_client/tests/test_client_read.py  TestGetEpic; JQL-регрессия test_jql_shape
src/jira_client/tests/test_no_secrets.py   TestLoginDoesNotLeak
```

**Создано (9):**

```
backend/app/integrations/__init__.py
backend/app/integrations/jira/__init__.py
backend/app/integrations/jira/port.py          JiraPort, SealedSession, SessionCheck
backend/app/integrations/jira/errors.py        единая карта ошибок, classify
backend/app/integrations/jira/adapter.py       JiraAdapter
backend/tests/conftest.py                      bootstrap sys.path + фикстуры MockTransport
backend/tests/test_jira_integration.py         39 тестов адаптера
src/jira_client/tests/test_login.py            22 теста входа по паролю
architecture/probes/KON-34_report.md           этот файл
```

**В `git status` есть записи, не относящиеся к KON-34** — они существовали
до меня и не изменялись:

```
M  architecture/06_plan_and_tasks.md   декомпозиция KON-31 от 06.10 (работа владельца)
?? .claude/agents/dev.md               агенты Claude, не входят в область записи
?? .claude/agents/qa.md
?? architecture/probes/KON-34_opencode_task.md   исходная постановка задачи
```

**Не правилось сознательно:** `architecture/03_adr.md` (ADR-005),
`architecture/probes/KON-30_jira_probe_report.md` — устаревшие формулировки
перечислены в разделе 8.

**Не коммичено и не запушено** — коммит делает владелец.

---

## 8. Открытые вопросы

### 8.1 Требуют решения владельца

| # | Вопрос | Где видно |
|---|---|---|
| **а** | `SA-2219` — статус «В процессе» при резолюции «Готово»: это особенность проекта или рассинхрон? | KON-30, раздел открытых вопросов |
| **б** | Канонично ли `customfield_10101` (`Epic Link`) при наличии `customfield_11032`? Сейчас выбрано `10101` — константой `JiraAdapter.epic_link_field`, замена в одном месте | `adapter.py:60`, `fields.EPIC_LINK` |
| **в** | 26 вхождений корпоративных e-mail в разделе C выгрузок — убрать или это ожидаемо | KON-30, раздел C |
| **г** | Удалить ли черновик `C:\Users\user\AppData\Local\Temp\opencode\jira_raw` (~2 MB)? | вне репозитория |
| **д** | Нужен ли PAT в `.env` как запасной путь? На 8.13.0 PAT невозможен | KON-30 |
| **е** | Docker-упаковка `src/jira_client` — см. п. 6.2 раздела 6 | `docker-compose*.yml` |
| **ж** | TTL **замерен** (раздел 5): простой ≈300 с, при опросе раз в 120 с ≥30 мин. Осталось сверить с консолью администратора Jira и убрать допущение «8 ч» из `06_plan_and_tasks.md:76` | раздел 5 |
| **з** | **Интервал опроса воркера.** FR-028 задаёт 5 минут, а это ровно таймаут сессии — первый опрос с вероятностью попадёт за границу (эксперимент №1: 401). Согласовать интервал ≤120 с: замер дал 15/15 `ok` за 30 мин | `05_deployment_security.md:48`, раздел 5.3 |
| **и** | Фоновый опрос без активного пользователя при TTL 5 мин нежизнеспособен: сессия умирает за 5 минут простоя. Возвращается открытый вопрос KON-30 (техническая учётка / Application Link / PAT) | раздел 5.3, п. 3 |

### 8.2 Устаревшие формулировки в смежных документах

Править их в KON-34 было **вне разрешённой области** — отмечено здесь, чтобы
владелец обновил при следующей правке:

| Файл, строка | Что устарело | Актуально |
|---|---|---|
| `architecture/03_adr.md:28` | «Для этого включается `SessionLoginProvider` (**флаг `enabled`**)» | флага `enabled` больше нет; вход — рабочая операция `login()`, включена решением владельца 05.10 |
| `architecture/probes/KON-30_jira_probe_report.md:150` | «`POST /rest/auth/1/session` — **заблокирован в коде** … `SessionLoginProvider` с `enabled=False`; попытка включить … поднимает `PermissionError`» | не действует с 06.10 |
| там же, стр. 169 | «`SessionLoginProvider` без явного решения владельца поднимает [PermissionError]» | не действует |
| там же, стр. 433 | «`auth.py` | … **заблокированный** `SessionLoginProvider`» | не действует |
| `architecture/06_plan_and_tasks.md:76` | «боевой адаптер включается **после** включения `SessionLoginProvider` владельцем jira_client» | включение состоялось 06.10 в KON-34 |
| `architecture/README.md:42` | «`SessionLoginProvider` включён этим решением» | **актуально**, совпадает с KON-34 |

Корректно и уже актуально: `architecture/README.md:42` и `:65`,
`architecture/04_nfr_matrix.md:13`, `architecture/05_deployment_security.md:47`.

### 8.3 Процессуальные

* Все изменения KON-30 и KON-34 **не закоммичены** (`git log` — последний
  `71d5053`).
* После пробы KON-30 рекомендовано **сменить пароль пользователя Jira**:
  пароль передавался в теле запросов пробы.
* **Секреты KON-34 лежат только в `.env`** (`.gitignore:9`): заполнены
  `JIRA_USERNAME`, `JIRA_PASSWORD` и `JIRA_TOKEN_ENCRYPTION_KEY` — владелец
  ввёл логин/пароль, ключ сгенерирован командой из п. 4.1 без вывода на
  экран. В репозиторий, в тесты, в отчёт и в журналы замеров ничего из этого
  не попало; в выводе живой проверки логин замаскирован, пароль печатался
  только длиной, sealed-токен — звёздочками.
* Журналы замеров сессии лежат в `%TEMP%` (п. 5.5) и в git не идут.
  При желании их можно удалить — в артефакты задачи они не входят.
