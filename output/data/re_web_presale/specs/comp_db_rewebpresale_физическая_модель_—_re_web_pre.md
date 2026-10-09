# Физическая модель БД re_web_presale (KON-29, обновлено KON-60)

PostgreSQL 16, две базы: `app_db` (схема `app`) и `cv_db` (схема `cv`), роли `app_rw` и `cv_rw` без доступа к чужой БД (ADR-003, NFR-007). Полный документ: `output/reports/re_web_presale/db_physical_model_KON-29.md`. Миграции: `db/migrations/`, тестовые данные: `db/seed/`.

## Изменения
- 09.10.2026, KON-60, миграция app_db V004: `app_user.display_name text NULL` (ФИО из LDAP `cn`, длина 1..256, обновляется при каждом входе; NULL — в интерфейсе показывается логин). E-mail не хранится.

## app_db
- `app_user` (включая `display_name`), `app_session` (хранится sha256 идентификатора), `llm_provider`, `app_setting`, `status_group` и `status_group_status` (группы статусов нормализованы, статус может входить в несколько групп).
- `secret_store`: шифртекст AES-256-GCM (ciphertext с тегом, nonce 12 байт, key_id) для сессионного токена Jira и ключей LLM; пароль Jira/LDAP не хранится нигде (ADR-005, ADR-006, NFR-006).
- Кэш Jira: `jira_epic` (Client, rules_cache, rules_fetched_at, TTL 1 час), `jira_need` (Need, поля Jira customfield_10101/10811/20809/20811), `jira_need_tag`, `sync_state` (курсор last_sync_at, штамп last_success_at, last_error, initial_load_done). Списки читаются только из кэша; индексы по (status, jira_updated_at), jira_created_at, epic_key, employee_key.

## cv_db
- `employee` (кэш карточки Jira, заметка с автором и датой), `employee_file` (метаданные, вид, архив), `employee_file_content` (bytea до 10 МБ, ADR-009), `employee_file_skill` (навыки основного CV), `recommendation` (кэш LLM по паре потребность + основное CV + модель), `sync_state`.
- Архив версий основного CV (FR-039): версии это строки employee_file с is_archived; одно актуальное на сотрудника (partial unique index); функции `cv.replace_main_cv` и `cv.restore_main_cv`; отложенный constraint trigger запрещает более 3 архивных версий; триггер запрещает удаление актуального основного CV (FR-040).

## Решения
Мягкие межбазовые ссылки: jira_need.employee_key, recommendation.need_key, employee_file.client_epic_key. JSON-атрибуты ERD нормализованы, JSONB только в jira_epic.rules_cache. Аудит-таблицы нет (NFR-005), автоудаления нет (NFR-004). Проверено на PostgreSQL 16.14: up, down, повторный up, seed и 40 проверок ограничений и ролей; V004 проверена на PostgreSQL 16 (применение на БД с данными, повторный up, down и повторный up); не проверено через настоящий psql и под нагрузкой.