# INDEX — реестр живой документации re_web_presale

Обновлён: 26.10.2026. Правила — `README.md`. Нарезка БФ — **предложение аналитика** (построена по 40 FR / 10 NFR / 9 BR / 6 UC); утверждает владелец.
Ссылки на требования — по ID (текст в слое 1: `output/data/re_web_presale/specs/`, граф — `…_traceability_repo.json`).

## Бизнес-функции

Статусы карточек: `idea` → `planned` → `in_dev` → `implemented` (правила — `README.md`). Для Arch берутся только `planned` с утверждёнными требованиями.

| БФ | Название | Требования | Компоненты | UI | Релиз | Карточка |
|---|---|---|---|---|---|---|
| BF-01 | Потребности: список, статусы, синхронизация с Jira, правила клиента | FR-001, 015, 020, 028, 029, 034, 035; NFR-002, 008; BR-005, 006, 008, 010 | `jira_integration`, app_db | KON-78 (каркас), KON-79 (статус) | MVP | [BF-01_needs_and_sync](01_business_functions/BF-01_needs_and_sync/README.md) |
| BF-02 | Сотрудники и резюме (CV, файлы, архив) | FR-002, 003, 004, 022, 027, 032, 033, 037, 039, 040; UC-003; NFR-007; ERD-001, DD-001 | cv_db | KON-78, KON-115 (заметка) | MVP | [BF-02_employees_and_cv](01_business_functions/BF-02_employees_and_cv/README.md) |
| BF-03 | Подбор кандидата: сопоставление и рекомендация (LLM) | FR-005, 023, 030, 036; NFR-010; BR-007 | llm_gateway | — | MVP | [BF-03_candidate_matching](01_business_functions/BF-03_candidate_matching/README.md) |
| BF-04 | Назначение кандидата на потребность | FR-008, 009, 010, 013, 018; UC-001; BR-001, 002, 003; BP-001 | `jira_integration` | KON-79 | MVP | [BF-04_assign_candidate](01_business_functions/BF-04_assign_candidate/README.md) |
| BF-05 | Смена статуса потребности | FR-011, 026; UC-002; BR-001, 008, 010 | `jira_integration` | KON-79 | MVP | [BF-05_change_status](01_business_functions/BF-05_change_status/README.md) |
| BF-06 | Вход и доступ (LDAP, допущенные, сессия Jira, настройки LLM) | FR-021, 024, 025; UC-005, 006; NFR-006 | `jira_integration` (сессия), auth | KON-78 | MVP | [BF-06_login_and_access](01_business_functions/BF-06_login_and_access/README.md) |
| BF-07 | Структура приложения и фирменный вид | FR-031, 038 | — | KON-78, KON-92 (шапка) | MVP | [BF-07_app_shell](01_business_functions/BF-07_app_shell/README.md) |
| BF-08 | Статистика ставок | FR-019 | — | — | MVP (в коде есть; сверить текст FR-019) | [BF-08_rate_statistics](01_business_functions/BF-08_rate_statistics/README.md) |
| BF-09 | CV по шаблону клиента и адаптация | FR-012, 017; UC-004 | — | KON-80 | Версия 2 | [BF-09_cv_by_client_template](01_business_functions/BF-09_cv_by_client_template/README.md) |
| BF-10 | Контроль качества CV и диалог с LLM | FR-006, 007, 014 | — | — | Версия 2 | [BF-10_cv_quality_and_dialog](01_business_functions/BF-10_cv_quality_and_dialog/README.md) |
| BF-11 | История отправок сотрудника | FR-016 | — | — | Версия 2 | [BF-11_send_history](01_business_functions/BF-11_send_history/README.md) |
| BF-12 | ROS: рентабельность кандидата | (нет, KON-121) | — | — | Версия 2 | [BF-12_ros_profitability](01_business_functions/BF-12_ros_profitability/README.md) |

Сквозные: NFR-001, 003, 004, 005, 009 — относятся к платформе (`architecture/04_nfr_matrix.md`).

## Системные компоненты

| Компонент | Папка | Статус документации | Диаграммы в графе 5.1 |
|---|---|---|---|
| Интеграция с Jira | `02_system_components/jira_integration/` | заполнен 26.10 (SRV, API ×2, SEQ ×6, MAP, FLV) | COMP-SEQ-JiraLoginSession, …IncrementalSync, …EpicRules, …AssignCandidate, …ChangeStatus, …SessionExpired |
| Аутентификация и сессии (LDAP) | `02_system_components/auth/` | обзорный паспорт 26.10 | SEQ-1, SEQ-6 (в `jira_integration`) |
| Файлы и CV, модель `cv_db` | `02_system_components/cv_db/` | `implemented`: SRV, MAP_tables, ERD (V001–V010); запись KON-29 в `output/reports/` устарела | COMP-DB-ReWebPresale |
| Шлюз LLM и сопоставление | `02_system_components/llm_gateway/` | обзорный паспорт 26.10 (пробы `architecture/probes/KON-34`, `KON-63`) | — |
| Модель данных app_db | `02_system_components/app_db/` | `implemented`: SRV, MAP_tables, ERD (V001–V008) | — |
| Развёртывание | — | `architecture/05_deployment_security.md`, `infra/` | — |

Паспорта auth и llm_gateway — обзорного уровня (по ADR, маршрутам и модулям кода, без построчной сверки и диаграмм); app_db и cv_db — с моделью данных по миграциям. Углублять по мере задач этапа 2: диаграммы входа и LLM-задачи, контракты эндпоинтов.

## Архитектура и дизайн (внешние точки входа)

* `architecture/` — C4 (L1/L2), стек, ADR-001…011, NFR-матрица, развёртывание, план, пробы.
* `design/KON-N/decision.md` — решения UX: KON-78 каркас, KON-79 «Назначить»/статус, KON-92 шапка, KON-115 заметка сотрудника (approved); KON-98 отменён (CR-008).

## Расхождения «требование ↔ реализация»

Открытых нет. Jira-расхождения D-1…D-8 закрыты CR-009 и CR-010 (26.10.2026; FR-025 v2.0, UC-005 v2.0, NFR-006 v1.1, DD-001 v1.1, NFR-009 v1.1, FR-028 v1.1, FR-026 v1.4). Пакет утверждения `APKG-CR009-010-CLOSE` (5.5) принят владельцем 26.10.2026, статусы в графе возвращены в `approved`. Не сверены: текст FR-019 с `rate_benchmark.py` (BF-08).

## Журнал (слой 1) — как читать

`output/reports/re_web_presale/`: `3_*` план, `4_*` выявление, `5_1…5_5` трассировка/изменения/утверждения, `6_*` стратегия, `7_*` спецификация и анализ. `5_4_cr_decision_CR-NNN_*` — решения по CR; `5_5_approval_package_*` — пакеты утверждения. Эти файлы — история и аудит; **текущее состояние читать здесь, в `product_docs/`**.
