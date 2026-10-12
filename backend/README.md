# backend

Python 3.12, FastAPI. Каркас: `app/main.py` (api, `/health`), `app/worker.py` (worker).
Локально: `pip install -r requirements-dev.txt; ruff check .; pytest`.

## Синхронизация Jira (KON-35)
Модуль `app/sync`, процесс `python -m app.worker`, API `/sync/*` (см. `openapi.json`).
Необязательные переменные (умолчания в `app/sync/settings.py`): `SYNC_NEEDS_INTERVAL_SEC` (300),
`SYNC_EMPLOYEES_INTERVAL_SEC` (3600), `SYNC_EPIC_TTL_SEC` (3600), `SYNC_INITIAL_LOAD_DAYS` (183),
`SYNC_PAUSE_RECHECK_SEC` (15), `JIRA_UTC_OFFSET_HOURS` (4), `JIRA_NEED_PROJECT`, `JIRA_NEED_ISSUETYPE`,
`JIRA_EMPLOYEE_PROJECT`, `JIRA_EMPLOYEE_ISSUETYPE`. Нужна миграция V005 (app_db и cv_db).
БД-тесты: `TEST_APP_DB_URL`, `TEST_CV_DB_URL` (роли app_rw / cv_rw, миграции app_db V001-V007 и cv_db V001-V006).

## Файлы сотрудников (KON-36)
Форматы заданы в одном месте, `app/cv/formats.py`, и отдаются в `GET /cv/limits` (`extensions_by_kind`).
CV (основное и доп.): только doc и docx, иначе 415; текст извлекается из doc/docx. Материалы: doc, docx,
pdf, txt, xls, xlsx, ppt, pptx, png, jpg, jpeg; текст не извлекается (статус `none`).

## Группы статусов (KON-64)
Модуль `app/status_groups`, API `/admin/status-groups` (только admin, CSRF на изменяющих): список групп с составом, статусами для выбора и статусами «Прочих»; создать, переименовать, удалить группу; добавить/убрать статус; порядок (`PUT /order`). «Прочие» и «Все статусы» зарезервированы и в таблицы не кладутся. БД-тест: `tests/unit/test_status_groups_db.py`.
