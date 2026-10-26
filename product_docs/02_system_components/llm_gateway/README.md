# Шлюз LLM и сопоставление — паспорт компонента (SRV)

Уровень детализации: **обзорный** (ADR-007, маршруты, модули кода). Построчной сверки и диаграмм нет; подробные пробы — `architecture/probes/KON-34`, `KON-63`.

| Атрибут | Значение |
|---|---|
| Назначение | Единая точка обращения к LLM: провайдеры, обезличивание, предпросмотр, асинхронные задачи, таймаут; поверх неё — сопоставление потребности с кандидатами и выделение навыков из CV |
| Код | `backend/app/llm_gateway/` (`providers.py`, `anonymize.py`, `ner.py`, `dictionaries.py`, `technologies.py`, `runner.py`, `service.py`, `router.py`), `backend/app/matching/` (`service.py`, `prompts.py`, `selection.py`, `semantic.py`, `screening.py`, `profile.py`, `settings.py`, `router.py`) |
| Процесс | задачи выполняет `worker` (тот же код, другой процесс) |
| Бизнес-функции | BF-03, (BF-09, BF-10 — этап 2) |
| Требования | FR-005, FR-023, FR-030, FR-036, NFR-010; BR-007 |
| Архитектурное решение | ADR-007 |

## Эндпоинты (по роутерам)

| Группа | Пути |
|---|---|
| Провайдеры | `GET /llm/providers`; админ: `GET/POST /admin/llm/providers`, `PATCH/DELETE /admin/llm/providers/{id}` |
| Словари обезличивания | `GET/PUT /admin/llm/dictionary`, `GET/PUT /admin/llm/tech-terms` |
| Задачи LLM | `POST /llm/tasks`, `GET /llm/tasks`, `GET /llm/tasks/{id}`, `POST …/confirm`, `…/cancel`, `…/retry` |
| Сопоставление | `POST /needs/{key}/match`, `GET /needs/{key}/match`, `POST …/match/next`, `GET /needs/match-counts`, `GET /matching/runs/{id}`, `POST …/confirm`, `…/cancel`, `PUT …`, `GET/PUT /admin/matching/settings` |
| Резюме и навыки | `GET /resume/employees`, `GET /resume/technologies`, `GET /resume/employees/{key}/profile`, `GET /resume/semantic-search`, `POST /resume/employees/{key}/skills`, `POST /resume/skills/backfill` |

## Ключевые правила (ADR-007)
* Все вызовы LLM — только через `llm_gateway`.
* Внутренняя Qwen (OpenAI-совместимый API, `http://ollama-gpu.simbirsoft.com:3000/api/v1`; ключ создаёт владелец, только env/секреты) — по умолчанию, без обезличивания; модель по умолчанию `qwen/qwen3.6-35b-a3b`, переопределяется `LLM_MODEL`.
* Внешний провайдер: конвейер обезличивания (regex, словари клиентов/ФИО/проектов, NER) → **обязательный предпросмотр и подтверждение**; плейсхолдеры живут только на время задачи (в `llm_task` шифртекстом AES-GCM) и очищаются при завершении/отмене.
* Асинхронные задачи, таймаут по умолчанию 120 с (NFR-010); UI не блокируется.
* Файлы для Jira обезличиванию не подвергаются.
* Известный дефект закрыт: маскирование фамилий в творительном падеже (KON-74).

## История изменений
| Дата | CR / задача | Что изменилось |
|---|---|---|
| 2026-10-26 | KON-128 | паспорт создан |
