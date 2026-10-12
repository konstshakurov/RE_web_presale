# backend

Python 3.12, FastAPI. Каркас: `app/main.py` (api, `/health`), `app/worker.py` (worker).
Локально: `pip install -r requirements-dev.txt; ruff check .; pytest`.

## Синхронизация Jira (KON-35)
Модуль `app/sync`, процесс `python -m app.worker`, API `/sync/*` (см. `openapi.json`).
Необязательные переменные (умолчания в `app/sync/settings.py`): `SYNC_NEEDS_INTERVAL_SEC` (300),
`SYNC_EMPLOYEES_INTERVAL_SEC` (3600), `SYNC_EPIC_TTL_SEC` (3600), `SYNC_INITIAL_LOAD_DAYS` (183),
`SYNC_PAUSE_RECHECK_SEC` (15), `JIRA_UTC_OFFSET_HOURS` (4), `JIRA_NEED_PROJECT`, `JIRA_NEED_ISSUETYPE`,
`JIRA_EMPLOYEE_PROJECT`, `JIRA_EMPLOYEE_ISSUETYPE`. Нужна миграция V005 (app_db и cv_db).
БД-тесты: `TEST_APP_DB_URL`, `TEST_CV_DB_URL` (роли app_rw / cv_rw, миграции app_db V001-V008 и cv_db V001-V007).

## Файлы сотрудников (KON-36)
Форматы заданы в одном месте, `app/cv/formats.py`, и отдаются в `GET /cv/limits` (`extensions_by_kind`).
CV (основное и доп.): только doc и docx, иначе 415; текст извлекается из doc/docx. Материалы: doc, docx,
pdf, txt, xls, xlsx, ppt, pptx, png, jpg, jpeg; текст не извлекается (статус `none`).

## Группы статусов (KON-64)
Модуль `app/status_groups`, API `/admin/status-groups` (только admin, CSRF на изменяющих): список групп с составом, статусами для выбора и статусами «Прочих»; создать, переименовать, удалить группу; добавить/убрать статус; порядок (`PUT /order`). «Прочие» и «Все статусы» зарезервированы и в таблицы не кладутся. БД-тест: `tests/unit/test_status_groups_db.py`.

## Шлюз LLM (KON-37)
Модуль `app/llm_gateway`; все обращения к LLM только через него (ADR-007). Миграция app_db V008 (колонка
`llm_provider.model`, очередь `llm_task`). Внутренняя Qwen (OpenAI-совместимый API) создаётся при старте из
`LLM_BASE_URL` (по умолчанию адрес из архитектуры), `LLM_MODEL` (по умолчанию `qwen/qwen3.6-35b-a3b`), ключ `LLM_API_KEY` (только env; ключи
админских провайдеров - шифртекстом в `secret_store`, `kind=llm_key`). Внешний провайдер: обезличивание
(regex, словари из кэша Jira и CV и админский словарь, natasha NER) -> предпросмотр -> подтверждение
(`POST /llm/tasks/{id}/confirm`) -> worker вызывает с таймаутом провайдера (по умолчанию 120 с) -> обратная
подстановка. Внутренний - без обезличивания, сразу в очередь. Повтор: `POST /llm/tasks/{id}/retry`.
API: `/llm/tasks*`, `/llm/providers` (выбор модели), `/admin/llm/providers*`, `/admin/llm/dictionary`.
Завершённые задачи хранятся `LLM_TASK_TTL_DAYS` (7) дней. Модуль вызывается из Python так:
`request.app.state.llm.submit(login, "match", prompt, provider_id=None, ref="ANALYTICS-1")`.
БД-тест: `tests/unit/test_llm_db.py`.

## Навыки из CV и сопоставление (KON-38)
Модуль `app/matching`; нужна миграция cv_db V007 (`file_skill_state`, `match_run`, `match_batch`,
`recommendation.note_snapshot_at`). Все вызовы LLM идут через шлюз (задачи `purpose=skills|match`);
результат забирает worker сразу после задачи и API лениво при запросе состояния (оба идемпотентны).
- Навыки (FR-027): ставятся при загрузке основного CV и по `POST /resume/employees/{key}/skills`,
  `POST /resume/skills/backfill`; хранятся по файлу, без замены файла не пересчитываются.
- Сопоставление (FR-005, FR-036): `POST /needs/{key}/match` (`provider_id`, `force`), `GET /needs/{key}/match`
  (`limit`=10, `offset` - «Показать ещё»). Отбор: допустимые статусы (6.2), основное CV с текстом, до 30
  кандидатов по совпадению навыков с текстом потребности; в LLM уходят пакетами по <=5, без ФИО и ключей.
  Кэш по потребность + основное CV + модель; устаревает при смене потребности, заметки или модели.
  Внешний провайдер: задачи в `awaiting_confirmation`, подтверждение `POST /llm/tasks/{id}/confirm` на каждую.
- «Резюме» (FR-032, FR-003): `GET /resume/employees` (`q`, `status`, `technology`, `match=all|any`),
  `GET /resume/technologies`, `GET /resume/employees/{key}/profile` (без LLM).
Тесты: `tests/unit/test_matching_*.py`; БД-тест `test_matching_db.py` (`TEST_CV_DB_URL`, `TEST_APP_DB_URL`,
cv_db V001-V007).
