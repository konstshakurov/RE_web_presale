# MAP — таблицы app_db (схема `app`)

Актуально по миграциям V001…V008 (26.10.2026). Колонки и ограничения целиком — в `db/migrations/app_db/`. Основа — раздел 4 `db_physical_model_KON-29.md` с поправками V004–V008.

| Таблица | Назначение | Ключ | Заметки | Требования |
|---|---|---|---|---|
| `app_user` | список допущенных | `ldap_login` | роль user/admin, `active`; `display_name` (ФИО из LDAP `cn`, 1..256, обновляется при входе; NULL — показывается логин; e-mail не хранится) — V004 | FR-021, FR-024 |
| `app_session` | серверные сессии | `session_hash` (sha256, 32 байта) | сам идентификатор не хранится; `expires_at > created_at`; TTL 8 ч | ADR-008 |
| `llm_provider` | LLM-провайдеры | `id` | уникальное имя, один `is_default` (partial unique), таймаут 1..3600 с; `model text` — V008 | FR-030, NFR-010 |
| `secret_store` | шифрованные секреты | `id` | `kind` `jira_session` / `llm_key`; владелец или провайдер строго по виду; nonce 12 байт, ciphertext ≥ 16 байт; одна запись на пользователя/провайдера | NFR-006 |
| `app_setting` | настройки | `key` | формат ключа `[a-z0-9_]`; (jql_needs, vat_percent, max_file_mb, размеры пакетов сопоставления) | — |
| `status_group`, `status_group_status` | группы статусов | `id`; `(group_id, status_name)` | статус может входить в несколько групп; 4 группы заполнены V007 (11 пар); «Прочие» и «Все статусы» виртуальные | FR-035 |
| `jira_epic` | эпик клиента с правилами | `epic_key` | `rules_cache jsonb` + `rules_fetched_at`; `summary` | FR-015 |
| `jira_need` | кэш потребности | `jira_key` | статус, эпик (FK), ставка (`rate_*`), `jira_created_at/updated_at`, `synced_at`; `employee_login` (V006, нижний регистр 1..128, «Профиль сотрудника» `customfield_10707`) и `employee_key` (ставится только если логин однозначно найден в `cv.employee.jira_login`, иначе NULL); валюты RUB/USD/KZT | FR-001, 028 |
| `jira_need_tag` | теги потребности | `(need_key, tag)` | теги в нижнем регистре | — |
| `sync_state` | состояние опроса потребностей | `scope='needs'` | курсор (`last_sync_at`), штамп (`last_success_at`), ошибка; `last_error_code` (V005): needs_reauthorization, no_permission, jira_unavailable, jira_error, not_enabled, error; NULL — последний опрос успешен | FR-028, NFR-008 |
| `llm_task` | очередь асинхронных задач LLM | `id` uuid | `owner_login` (FK), `purpose`, `ref`, `provider_id` (FK), `is_external`, статусы `awaiting_confirmation → queued → running → done / failed / timeout / cancelled`; `payload_ct`/`result_ct` шифртекстом AES-256-GCM (nonce 12 байт, `key_id`), payload обнуляется по завершении; `error_code` без секретов и текстов запроса; индексы под очередь (SKIP LOCKED) и «мои задачи» — V008 | FR-023, FR-030, NFR-010 |

## Трассировка логической модели (ERD-001) → таблицы app_db

| Сущность ERD-001 | Таблица |
|---|---|
| Need | `jira_need` (+ `jira_need_tag`); группа статуса вычисляется join-ом по `status_group_status`, не хранится |
| Client | `jira_epic` |
| Employee | не в этой БД — `cv.employee` (см. `../cv_db/MAP_tables.md`) |
| Recommendation | не в этой БД — `cv.recommendation` |
| AppUser / Session / Settings / LlmProvider | `app_user`, `app_session`, `app_setting`, `llm_provider` + `secret_store` |
