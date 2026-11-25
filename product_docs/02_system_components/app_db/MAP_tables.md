# MAP — таблицы app_db (схема `app`)

Актуально по миграциям V001…V010 (23.11.2026). Колонки и ограничения целиком — в `db/migrations/app_db/`. Основа — раздел 4 `db_physical_model_KON-29.md` с поправками V004–V010.

| Таблица | Назначение | Ключ | Заметки | Требования |
|---|---|---|---|---|
| `app_user` | список допущенных | `ldap_login` | роль user/admin, `active`; `display_name` (ФИО из LDAP `cn`, 1..256, обновляется при входе; NULL — показывается логин; e-mail не хранится) — V004 | FR-021, FR-024 |
| `app_session` | серверные сессии | `session_hash` (sha256, 32 байта) | сам идентификатор не хранится; `expires_at > created_at`; TTL 8 ч | ADR-008 |
| `llm_provider` | LLM-провайдеры | `id` | уникальное имя, один `is_default` (partial unique), таймаут 1..3600 с; `model text` — V008 | FR-030, NFR-010 |
| `secret_store` | шифрованные секреты | `id` | `kind` `jira_session` / `llm_key`; владелец или провайдер строго по виду; nonce 12 байт, ciphertext ≥ 16 байт; одна запись на пользователя/провайдера | NFR-006 |
| `app_setting` | настройки | `key` | формат ключа `[a-z0-9_]`; (jql_needs, vat_percent, max_file_mb, размеры пакетов сопоставления) | — |
| `status_group`, `status_group_status` | группы статусов | `id`; `(group_id, status_name)` | статус может входить в несколько групп; 4 группы заполнены V007 (11 пар); «Прочие» и «Все статусы» виртуальные | FR-035 |
| `jira_epic` | эпик клиента с полевыми правилами | `epic_key` | `rules_cache jsonb` + `rules_fetched_at`; `summary` | FR-015 |
| `epic_comment` | синхронизированные комментарии эпика | `(epic_key, comment_id)` | `author`, `updated_at`, `body`, `fetched_at`; FK на `jira_epic`; V010, upsert по id | FR-015, FR-028 |
| `epic_rules_cache` | кэш LLM-тезисов правил клиента | `epic_key` | `comments_hash`, `theses_json`, `generated_at`, `provider_name`; FK на `jira_epic`; инвалидируется сравнением хеша, V010 | FR-015, FR-023 |
| `jira_need` | кэш потребности | `jira_key` | статус, эпик (FK), ставка (`rate_*`), `jira_created_at/updated_at`, `synced_at`; `employee_login` (V006, нижний регистр 1..128, «Профиль сотрудника» `customfield_10707`) и `employee_key` (ставится только если логин однозначно найден в `cv.employee.jira_login`, иначе NULL); валюты RUB/USD/KZT | FR-001, 028 |
| `jira_need_tag` | теги потребности | `(need_key, tag)` | теги в нижнем регистре | — |
| `sync_state` | состояние фоновых областей опроса | `scope` (`needs`, `employees`, `epic_comments`) | курсор/штамп (`last_sync_at`, `last_success_at`), ошибка; для `epic_comments` период по умолчанию 24 ч | FR-028, NFR-008 |
| `llm_task` | очередь асинхронных задач LLM | `id` uuid | `owner_login` (FK), `purpose` (`epic_rules` для тезисов клиента), `ref` с хешем комментариев, `provider_id` (FK), `is_external`, статусы `awaiting_confirmation → queued → running → done / failed / timeout / cancelled`; зашифрованные payload/result — V008 | FR-023, FR-030, NFR-010, FR-015 |
| `cvtpl_template` | временный файл шаблона клиента между шагами LLM | `task_id` (FK, PK) | `owner_login`, `file_name`, `content`, `created_at`; V009, каскадное удаление с LLM-задачей | FR-017 |

## Трассировка логической модели (ERD-001) → таблицы app_db

| Сущность ERD-001 | Таблица |
|---|---|
| Need | `jira_need` (+ `jira_need_tag`); группа статуса вычисляется join-ом по `status_group_status`, не хранится |
| Client | `jira_epic` |
| Employee | не в этой БД — `cv.employee` (см. `../cv_db/MAP_tables.md`) |
| Recommendation | не в этой БД — `cv.recommendation` |
| AppUser / Session / Settings / LlmProvider | `app_user`, `app_session`, `app_setting`, `llm_provider` + `secret_store` |
