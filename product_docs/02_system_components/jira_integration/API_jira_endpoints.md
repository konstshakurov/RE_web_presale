# API — вызовы Jira (исходящие контракты)

Источник: `src/jira_client/client.py`, `auth.py`, `backend/app/integrations/jira/adapter.py`, пробы KON-30/34/42/99.
Общие правила: `Content-Type: application/json` (кроме вложений); cookie сессии (`JSESSIONID`) — токен пользователя;
`User-Agent: re_web_presale-jira-client/1.0`; таймаут 10 с на вызов; повторов нет; TLS проверяется.
Все обращения идут через `JiraPort`; ошибки сводятся к единой карте (`FLV_errors.md`).

## Сводка

| # | Операция порта | HTTP | Назначение | Требования |
|---|---|---|---|---|
| J1 | `login` | `POST /rest/auth/1/session` | создать сессию | FR-025, UC-005 |
| J2 | `check_session` | `GET /rest/api/2/myself` | живость сессии, логин | FR-029 |
| J3 | `search_incremental` | `GET /rest/api/2/search` | инкрементальный опрос | FR-028, NFR-008 |
| J4 | `get_issue` | `GET /rest/api/2/issue/{key}` | перечитать потребность / сотрудника / эпик-поля | FR-028, FR-015 |
| J5 | `get_epic` | `GET /rest/agile/1.0/epic/{key}` | имя эпика | FR-015 |
| J6 | `get_epic_children` | `GET /rest/agile/1.0/epic/{key}/issue` | потребности эпика | FR-015, FR-028 |
| J7 | `get_transitions` | `GET /rest/api/2/issue/{key}/transitions?expand=transitions.fields` | доступные переходы и их поля | FR-011, FR-026 |
| J8 | `do_transition` | `POST /rest/api/2/issue/{key}/transitions` | смена статуса | FR-008, FR-011, FR-026 |
| J9 | `set_fields` / `assign_candidate` | `PUT /rest/api/2/issue/{key}` | исполнитель, «Профиль сотрудника», `customfield_11506` | FR-008, FR-009 |
| J10 | `add_comment` | `POST /rest/api/2/issue/{key}/comment` | комментарий (когда нет перехода) | FR-013 |
| J11 | `attach_file` | `POST /rest/api/2/issue/{key}/attachments` | CV и материалы | FR-008 |
| J12 | `set_rate` | `POST /rest/scriptrunner/latest/custom/rateCalculationLogic` | «Планируемая ставка» (кнопка «Ставка») | FR-018, CR-008 |

## J1 — `POST /rest/auth/1/session`

* Тело (плоское): `{"username":"<логин>","password":"<пароль>"}`. Вложенная форма `{"session":{…}}` → 400.
* Успех 200: `{"session":{"name":"JSESSIONID","value":"…"}}` → токен запечатывается и сохраняется (см. README).
* Пароль живёт только в теле запроса; не логируется.
* Отказы: 401 → `auth_failed`; ответ с признаком CAPTCHA/блокировки → `login_blocked`; сеть/TLS/таймаут/5xx → `jira_unavailable`.

## J2 — `GET /rest/api/2/myself`

Ответ 200 → сессия `ok`. 401 `login_required`/`invalid_cookie_or_expired` → `needs_reauthorization`; 403 → `no_permission`; сеть/5xx → `jira_unavailable`.
Также даёт логин пользователя (`name`) для ScriptRunner (J12), если он не передан.

## J3 — `GET /rest/api/2/search`

| Параметр | Значение |
|---|---|
| `jql` потребности | `project = ANALYTICS AND issuetype = "Аналитик" AND updated > "YYYY-MM-DD HH:mm" ORDER BY updated ASC, key ASC` |
| `jql` сотрудники | `project = ANALYTICS AND issuetype = "Epic" AND updated > "2000-01-01 00:00" …` (полная выгрузка раз в час) |
| `fields` | `NEED_FIELDS` / `EMPLOYEE_FIELDS` (см. `MAP_fields.md`); без списка Jira отдаёт сотни полей |
| `startAt`, `maxResults` | страницы по 100; лимит страниц 200 |
| Курсор | `last_sync_at − overlap(60 с)`, не опережает самую свежую строку кэша; первый запуск — `now − 183 дня`; время в поясе Jira (`+4`) |

Сортировка `updated ASC` обязательна: иначе водяной знак не двигается и часть задач теряется.

## J4 — `GET /rest/api/2/issue/{key}?fields=…`

Перед любой записью потребность перечитывается (FR-028) и после неё — ещё раз (обновление кэша; отказ перечитывания операцию не отменяет).

## J5 / J6 — Agile API

* `GET /rest/agile/1.0/epic/{key}` — плоский объект `{id,key,name,summary,done,color,self}`; `fields` игнорируется.
* `GET /rest/agile/1.0/epic/{key}/issue?startAt&maxResults≤100&fields=…` — дети эпика всех направлений; приложение отбирает потребности аналитики (`project=ANALYTICS`, `issuetype="Аналитик"`). JQL `parent = …` не использовать (0 результатов).
* Правила клиента — поля эпика (`GET /issue/{epic}?fields=summary,customfield_12000,…`, 13 полей, `MAP_fields.md`).

## J7 — `GET /rest/api/2/issue/{key}/transitions?expand=transitions.fields`

Ответ: `transitions[{id,name,to{name},fields{<id>:{name,required,allowedValues[{id,name}]}}}]`.
Список и есть разрешённые действия пользователя (права определяет Jira). Известные id (проба KON-30/42): 21 «Кандидат назначен», 171 «Кандидатов нет», 801 «Найм»; в коде id **не зашиты** — переход ищется по имени целевого статуса.

## J8 — `POST /rest/api/2/issue/{key}/transitions`

```json
{
  "transition": {"id": "21"},
  "fields": {"customfield_10707": {"name": "<логин>"}, "assignee": {"name": "<логин>"},
             "customfield_11602": {"id": "<id «Не требуется»>"}},
  "update": {"comment": [{"add": {"body": "<текст>"}}]}
}
```

* Успех: **204**.
* Комментарий только через `update.comment[].add`; для «Кандидатов нет» обязателен (Jira: 400 «Необходимо добавить комментарий»).
* Обязательные поля проверяет сервис по свежему `get_transitions` до отправки (повторного запроса переходов в адаптере нет — `validate=False`, NFR-009).
* 500 на неизвестный/недоступный id → `jira_error` («переход неизвестен или недоступен из текущего статуса»).
* Особый случай перехода 21 при `11506 = Да` — см. `SEQ_sequences.md` (SEQ-4), CR-006.

## J9 — `PUT /rest/api/2/issue/{key}`

Тело `{"fields": {…}}`, ответ 204. Применения: `assignee: {"name": "<логин пользователя сессии>"}` (FR-009), «Профиль сотрудника» `customfield_10707: {"name": "<логин сотрудника>"}` (если поля нет на экране перехода), `customfield_11506: {"value": "Нет"|"Да"}` (обход CR-006). Пустой набор полей клиент отклоняет до отправки.

## J10 — `POST /rest/api/2/issue/{key}/comment`

Тело `{"body": "<текст>"}`; пустой комментарий отклоняется до отправки. Используется только когда перехода «Кандидат назначен» не требуется (потребность уже в этом статусе, FR-010); иначе комментарий уходит вместе с переходом (J8).

## J11 — `POST /rest/api/2/issue/{key}/attachments`

`multipart/form-data`, поле `file`; обязательный заголовок `X-Atlassian-Token: no-check`. Своего лимита размера приложение не вводит (`size_limit=0`), размер судит Jira; в самом приложении лимит файла CV — 10 МБ (допущение A5). Каждый файл — отдельный шаг (`attach:<id>`), повтор не дублирует уже загруженные.

## J12 — `POST /rest/scriptrunner/latest/custom/rateCalculationLogic` (кнопка «Ставка»)

```json
{"issueKey":"ANALYTICS-123","currentUserUsername":"<логин>",
 "customfield_21105":"22412",          // кто предлагает: 22412 Производство
 "customfield_20809":"3500",           // ставка
 "customfield_20810":"20999",          // НДС: 20999 Да / 21000 Нет
 "customfield_20811":"21001",          // валюта: 21001 RUB
 "customfield_20812":"21006"}          // единица: 21006 час
```

Заголовок `Content-Type: application/json;charset=utf-8`. Поведение (проба KON-99):

* ответ **200 не гарантирует запись** (неверный id справочника или нет `21105`) — адаптер: (1) проверяет id по белому списку до отправки, (2) не шлёт пустую/нечисловую/отрицательную/>1 000 000 ставку, (3) после записи перечитывает `20809/20810/20811/20812` и сверяет; расхождение → `jira_error «ставка не записана»`;
* 500 (пустая ставка, несуществующий ключ) → `jira_error`, не `jira_unavailable`;
* вызывается только после успешного назначения и только при «Кто предлагает ставку» = «Производство»; сбой → частичный успех (`rate_pending`).

Справочники (id → значение): proposer 22412 Производство / 22411 Клиент; НДС 20999 Да / 21000 Нет; валюта 21001 RUB, 21002 EUR, 21003 GBR, 21004 KZT, 21005 USD; единица 21006 час. Допускаются к отправке: НДС Да/Нет, валюты RUB/EUR/GBR/KZT/USD, единица «час», proposer — только «Производство».
