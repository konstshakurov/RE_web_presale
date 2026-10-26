# MAP — таблицы cv_db (схема `cv`)

Актуально по миграциям V001…V010 (26.10.2026). Колонки и ограничения целиком — в `db/migrations/cv_db/`. Основа — раздел 4 и 7 `db_physical_model_KON-29.md` с поправками V005–V010.

## Данные сотрудников и файлов

| Таблица | Назначение | Ключ | Заметки | Требования |
|---|---|---|---|---|
| `employee` | кэш карточки сотрудника из Jira + заметка | `jira_key` | заметка всегда с автором и датой (FR-033); `jira_login` («Профиль сотрудника» 10707, не UNIQUE: дубль = неоднозначное сопоставление; нижний регистр 1..128), `vacation_plan` («Отпуск» 10814 как есть); «описание» FR-002 = `extra_experience` (13504); `customfield_11515` — числовой id, не хранится — V006 | FR-002, 003, 033 |
| `employee_file` | метаданные файлов | `id` | вид (`main_cv`, `extra_cv`, `materials`, …), клиент шаблона, `is_archived`, `archived_at`, извлечённый текст; правила архива см. ниже | FR-004, 022, 039, 040 |
| `employee_file_content` | байты файла | `file_id` | 1..10 МБ (CHECK) | FR-004, ADR-009 |
| `employee_file_skill` | навыки основного CV | `(file_id, tag)` | пересчёт при замене файла | FR-027 |
| `sync_state` | состояние опроса сотрудников | `scope='employees'` | штамп «актуально на»; `last_error_code` (V005) | FR-028 |

Архив основного CV (V003, FR-039): активным может быть только одно основное CV сотрудника; прежние версии архивируются, хранятся 3 последние; удаление актуального основного CV запрещено, восстановление из архива допускается.

## Сопоставление и поиск (V002, V007–V010; FR-005, FR-032, FR-033, FR-036, FR-023)

| Таблица | Назначение | Ключ | Заметки |
|---|---|---|---|
| `recommendation` | кэш глубокой оценки «потребность × основное CV × модель» | `id` | уникально `(need_key, cv_file_id, model)`; 0..100; составной FK только на основное CV того же сотрудника; `note_snapshot_at` (V007: правка заметки делает оценку устаревшей); `gaps jsonb` (V010: «чего не хватает до 100%», до 5 формулировок ≤ 200 знаков; NULL — не рассчитано, `[]` — полное совпадение) |
| `file_skill_state` | состояние извлечения навыков файла | `file_id` | `pending / ok / failed`, `task_id` (мягкая ссылка на `app.llm_task`), `model`; навыки не пересчитываются бесконечно, замена файла = новый `file_id` (V007) |
| `match_run` | прогон «Сопоставить» по потребности | `need_key` | `requested_by`, `provider_id` (мягкая ссылка), `model`, `need_jira_updated_at`; `chain_state` (V009): `pending / pending_force / done` — ступень 2 после скрининга |
| `match_batch` | пакеты кандидатов прогона | `id` | одна задача LLM = один пакет (`task_id` уникален); `employee_keys[]` и `file_ids[]` одинаковой длины; `status` pending/done/failed; `stage` (`screen`/`deep`) и `run_id` (V009) — подтверждение и отмена прогона целиком (один предпросмотр на прогон для внешнего провайдера) |
| `match_screen` | кэш предварительных баллов скрининга по навыкам (V009) | — | `skills_hash`: изменение набора навыков = пересчёт; текст CV и заметка в скрининг не уходят |
| `search_run` | прогон смыслового поиска в «Резюме» (V008) | `(requested_by, query_hash)` | запрос как есть (1..1000 знаков) + хэш; `scope jsonb` (V009: фильтры запуска), `chain_state` |
| `search_batch` | пакеты поиска | `id` | как `match_batch`; FK на `search_run`; `stage`, `run_id` |
| `search_result` | кэш оценок «запрос × основное CV × модель» | `(query_hash, cv_file_id, model)` | общий для пользователей; `note_snapshot_at`, `gaps jsonb` (V010); составной FK на основное CV |
| `search_screen` | кэш скрининга для поиска (V009) | — | аналог `match_screen` |

Запросы и оценки поиска хранятся ограниченное время: приложение чистит записи старше 30 суток.

## Трассировка логической модели (ERD-001) → таблицы cv_db

| Сущность ERD-001 | Таблица |
|---|---|
| Employee | `employee` |
| EmployeeFile | `employee_file` (+ `employee_file_content`, `employee_file_skill`) |
| Recommendation | `recommendation` |
| Need, Client | не в этой БД — `app.jira_need`, `app.jira_epic` (см. `../app_db/MAP_tables.md`) |

Мягкие ссылки (ADR-003): `recommendation.need_key` → `app.jira_need.jira_key`; `employee_file.client_epic_key` → `app.jira_epic.epic_key`; `*.task_id` → `app.llm_task.id`; `*.provider_id` → `app.llm_provider.id`.
