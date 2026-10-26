# app_db — паспорт компонента (SRV)

Статус страницы: `implemented`. Актуально на 26.10.2026 по миграциям `db/migrations/app_db/V001…V008`.

| Атрибут | Значение |
|---|---|
| Назначение | Пользователи и сессии приложения, шифрованные секреты, настройки, кэш Jira (потребности, эпики, состояние опроса), группы статусов, провайдеры и очередь задач LLM |
| СУБД | PostgreSQL 16, база `app_db`, схема `app` (служебная `meta.schema_migration`) |
| Роль приложения | `app_rw` (DML, не видит `cv_db`); миграции выполняются владельцем БД, не `app_rw` |
| Код | `backend/app/app_repo/`, `backend/app/sync/repo.py`, `backend/app/secrets/store.py`, `backend/app/llm_gateway/repo.py`, `backend/app/status_groups/repo.py`, `backend/app/auth/` |
| Бизнес-функции | BF-01, BF-03, BF-06 |
| Требования | FR-015, 021, 028, 030, 035; NFR-004, 005, 006, 007, 008, 010; ERD-001, DD-001 (логическая модель — слой 1) |
| Архитектурное решение | ADR-003 (две БД), ADR-004, ADR-005, ADR-006, ADR-007, ADR-008, ADR-011 |
| Первоисточник схемы | миграции в `db/migrations/app_db/` (эта страница объясняет, не дублирует DDL) |

## Состав страницы
* `MAP_tables.md` — таблицы, ключи, ограничения, трассировка к требованиям.
* `ERD_app_db.md` — диаграмма (Mermaid).

## Миграции

| Версия | Содержание | Задача |
|---|---|---|
| V001 | метаданные, пользователи, сессии, секреты, настройки, провайдеры LLM, группы статусов | KON-29 |
| V002 | кэш Jira: эпики, потребности, теги, состояние опроса | KON-29 |
| V003 | права `app_rw` | KON-29 |
| V004 | `app_user.display_name` (ФИО из LDAP) | KON-60 |
| V005 | `sync_state.last_error_code` | KON-35 |
| V006 | `jira_need.employee_login` («Профиль сотрудника») | KON-61 |
| V007 | заполнение 4 групп статусов | KON-61 |
| V008 | `llm_provider.model`, очередь `llm_task` | KON-37 |

Порядок применения: `db/migrations/bootstrap/00_roles_and_databases.sql` (один раз, суперпользователь) → `db/migrations/apply.sh up app_db` (ведёт `meta.schema_migration`); откат — `*.down.sql`. Тестовые данные — `db/seed/`.

## Правила
* Межбазовых FK нет — только мягкие ссылки по ключу (ADR-003): `jira_need.employee_key` → `cv.employee.jira_key`, `llm_task` ← `cv.*.task_id`, `cv.*.provider_id` → `llm_provider.id`.
* Пароли Jira/LDAP нигде не хранятся; сессионный токен Jira и ключи LLM — шифртекстом AES-256-GCM в `secret_store` (`key_id` для ротации).
* Аудит-таблицы и автоудаления нет (NFR-005, NFR-004); пользователь «удаляется» флагом `active=false`.
* `llm_task.payload_ct` очищается после завершения или отмены задачи (подстановки обезличивания живут только на время задачи).

## Известные расхождения
* `output/reports/re_web_presale/db_physical_model_KON-29.md` отражает состояние **до V008** (нет `llm_task`, `llm_provider.model`); как запись о KON-29 оставлен без изменений. Актуальное состояние — здесь.
* DD-001: поле `jira_token_encrypted` («OAuth-токен») фактически реализовано как `secret_store(kind='jira_session')` — терминология приведена в соответствие CR-009: DD-001 v1.1.

## История изменений
| Дата | Задача | Что изменилось |
|---|---|---|
| 2026-10-26 | KON-129 | страница создана из KON-29 и миграций V001–V008 |
