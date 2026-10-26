# API — эндпоинты приложения, работающие с Jira

Источник: `backend/app/{auth,sync,assign}/router.py`, `backend/openapi.json` (экспорт `app.export_openapi`).
Пути даны как в OpenAPI (без внешнего префикса proxy). Аутентификация — серверная сессия (cookie HttpOnly), изменяющие вызовы — с CSRF-токеном.
Зависимость `require_jira_ok` блокирует вызов, если сессия Jira пользователя не `ok`: **409** (`needs_reauthorization`/отказ с текстом), **503** (`unavailable`).

| Метод и путь | Зависимость | Назначение | Требования |
|---|---|---|---|
| `POST /auth/login` | — | LDAP bind → список допущенных → серверная сессия → хук создания сессии Jira | FR-024, FR-025, UC-005 |
| `POST /auth/jira-relogin` | сессия + CSRF | повторный ввод пароля при истёкшей сессии Jira (пароль не сохраняется; отказ Jira → `jira_status` в ответе, а не ошибка HTTP) | FR-025, FR-029 |
| `GET /auth/me` | сессия | профиль, роль, `jira_status` (`ok / not_checked / needs_reauthorization / …`) | FR-029 |
| `GET /sync/needs`, `GET /sync/needs/groups` | сессия | потребности из кэша (фильтры, страницы, группы статусов) + штамп `sync` | FR-001, 034, 035, 020 |
| `GET /sync/employees` | сессия | сотрудники из кэша + штамп | FR-002, 032 |
| `GET /sync/status` | сессия | штампы обеих областей: `state`, `stale`, `last_success_at`, `paused`, `message` | FR-028, NFR-008 |
| `POST /sync/refresh` | `require_jira_ok` | немедленный инкрементальный опрос токеном пользователя; `scope = needs \| employees` | FR-028, FR-034 |
| `GET /sync/epics/{epic_key}?refresh=` | сессия | эпик, правила клиента, число потребностей; кэш 1 час; при отказе Jira и наличии кэша — кэш с `stale=true` и `error` | FR-015 |
| `GET /needs/{key}/status-actions` | `require_jira_ok` | белый список статус-действий ∩ переходы Jira, поля и справочники из ответа Jira | FR-011, 026 |
| `POST /needs/{key}/status` | `require_jira_ok` | сменить статус (`search \| hire \| no_candidates`) | FR-011, 026, UC-002 |
| `POST /needs/{key}/assign` | `require_jira_ok` | назначить кандидата (профиль, исполнитель, вложения, комментарий, переход 21, ставка) | FR-008–010, 013, 018, UC-001 |

Ключ потребности: `^[A-Z][A-Z0-9_]*-[0-9]+$`.

## Тела запросов

* `POST /sync/refresh`: `{"scope":"needs"|"employees"}`. Ответ 200: `{scope, ok, fetched, upserted, skipped, busy, sync}`; `busy=true` — опрос уже идёт.
* `POST /needs/{key}/status`: `{"action":"search|hire|no_candidates","comment":"≤32000","fields":{"<field_id>":"<id значения Jira>"}}`; для `no_candidates` обязательны причина (`customfield_16100`) и комментарий. Ставка здесь **не** вводится (CR-008).
* `POST /needs/{key}/assign`: `{"employee_key":"…","resume_file_id":null|int,"extra_file_ids":[…],"rate_rub":null|0<x≤1 000 000,"comment":"≤32000","completed":["<шаг>",…]}`. `rate_rub` обязательна, если «Кто предлагает ставку» = «Производство»; при «Клиент» не отправляется.
* Ответ `ResultOut` (assign/status): `{need_key, status, refreshed, steps[], warnings[], rate_pending}`.

## Коды ошибок записи (`assign/router._HTTP`)

Тело ошибки: `{"detail":{"state","code","message","step","completed"}}`. `message` — текст Jira как есть (после `redact`, ≤2000 символов).

| `code` | HTTP | `state` | Смысл |
|---|---|---|---|
| `needs_reauthorization`, `auth_failed` | 409 | `needs_reauthorization` | сессия Jira истекла — запросить пароль (`/auth/jira-relogin`), повтор с `completed` |
| `login_blocked` | 409 | `blocked` | Jira требует подтверждения входа в браузере |
| `transition_unavailable`, `assign_unavailable` | 409 | `conflict` | Jira не предлагает переход из текущего статуса (кто-то уже сменил) |
| `no_permission` | 403 | `no_permission` | прав в Jira нет; повторный вход не поможет |
| `jira_error` | 502 | `blocked` | Jira отклонила запрос; показать текст |
| `jira_unavailable`, `not_enabled` | 503 | `unavailable` / `not_enabled` | Jira недоступна / запись выключена |
| `need_not_found`, `employee_not_found`, `file_not_found` | 404 | `not_found` | |
| прочие (`bad_request`, `rate_required`, `employee_no_profile`, `support_option_missing`…) | 422 | `invalid` | проверка до записи |

## Идемпотентность и частичный успех (назначение)

Назначение — последовательность шагов: `fields → attach:<id>… → transition (с комментарием) / comment → rate`.
При сбое посреди ответ несёт `completed` (выполненные шаги); повтор того же запроса с `completed` не дублирует вложения и комментарии. Сбой только на шаге `rate` — **не откат**: ответ 200 с `rate_pending=true` и предупреждением (FR-029); истёкшая сессия на любом шаге — 409 с `completed`.

## Наблюдения о контракте (для QA и аналитика)

* В `openapi.json` у `POST /needs/{key}/assign|status` и `GET /needs/{key}/status-actions` объявлены 200/401/403/422; фактически возвращаются также 404, 409, 502, 503 (см. таблицу). Документировать в OpenAPI через `responses=` — задача Dev (низкий приоритет).
* `GET /sync/*` (чтение кэша) не требуют живой сессии Jira — только входа в приложение.
