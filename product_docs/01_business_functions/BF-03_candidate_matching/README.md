# BF-03 — Подбор кандидата: сопоставление и рекомендация (LLM)

| Атрибут | Значение |
|---|---|
| Версия БФ | 1.0 |
| Статус | implemented (MVP, принято) |
| Релиз | MVP |
| Описание | Сопоставление потребности с кандидатами по основному CV и навыкам: процент соответствия и обоснование, асинхронная задача LLM, обезличивание для внешних провайдеров, предпросмотр и подтверждение отправки, настройка провайдеров. |

## Требования (слой 1; текст не копируется)

| ID | Название | Версия / статус (граф 5.1) | Спецификация |
|---|---|---|---|
| BR-007 | Корпоративный формат CV | 1.0 / approved | [`br_007_корпоративный_формат_cv.md`](../../../output/data/re_web_presale/specs/br_007_корпоративный_формат_cv.md) |
| FR-005 | Рекомендация кандидата с % соответствия и обоснованием | 1.0 / approved | [`fr_005_рекомендация_кандидата_с_%_соо.md`](../../../output/data/re_web_presale/specs/fr_005_рекомендация_кандидата_с_%_соо.md) |
| FR-023 | Обезличивание текста перед отправкой во внешнюю LLM | 1.0 / approved | [`fr_023_обезличивание_текста_перед_отп.md`](../../../output/data/re_web_presale/specs/fr_023_обезличивание_текста_перед_отп.md) |
| FR-030 | Настройка LLM-провайдеров | 1.0 / approved | [`fr_030_настройка_llm-провайдеров.md`](../../../output/data/re_web_presale/specs/fr_030_настройка_llm-провайдеров.md) |
| FR-036 | Сопоставление потребности с кандидатами | 1.0 / approved | [`fr_036_сопоставление_потребности_с_ка.md`](../../../output/data/re_web_presale/specs/fr_036_сопоставление_потребности_с_ка.md) |
| NFR-010 | Таймаут LLM | 1.0 / approved | [`nfr_010_таймаут_llm.md`](../../../output/data/re_web_presale/specs/nfr_010_таймаут_llm.md) |

## UI
* KON-78

(решения — `design/<KON-N>/decision.md`)

## Техническое решение (C3): компоненты
* llm_gateway и matching (см. `02_system_components/llm_gateway/`)
* app_db (`llm_provider`, `llm_task`; см. `02_system_components/app_db/`)

Архитектура: `architecture/01_c4.md`, `architecture/03_adr.md`.

## Реализация
Код:
* backend/app/llm_gateway/
* backend/app/matching/

Тесты:
* backend/tests/unit/test_llm_*.py
* backend/tests/unit/test_matching_*.py
* backend/tests/qa/test_stage1_llm_qa.py
* backend/tests/qa/test_stage1_match_qa.py
* backend/tests/qa/test_fr023_dataset_qa.py

Задачи Linear:
* KON-37 (шлюз LLM)
* KON-38 (навыки, сопоставление)
* KON-41 (frontend)

## История изменений
| Версия БФ | Дата | CR / задача | Что изменилось |
|---|---|---|---|
| 1.0 | 2026-10-26 | KON-128 | карточка создана по baseline v1.0 и CR-001…008 |

## Известные расхождения
—
