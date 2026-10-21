# Приёмка этапа 2: KON-43, KON-44, KON-95, KON-96, KON-97

Дата: 21.10.2026. Ветка `ux/kon-78-ui-refresh`, рабочее дерево. Метод: сверка критериев приёмки из Linear с кодом, тестами и отчётами QA (`qa/KON-100_acceptance.md`, `qa/KON-101_live_report.md`), прогон целевых тестов. В Jira не писал и не входил; код продукта не менял. Учтено: CR-008 заменил часть KON-97 (ставка вносится при назначении отдельным вызовом ScriptRunner `set_rate`, на переходе 801 ставка не передаётся) — это не дефект.

## Итог

| Задача | Вердикт |
|---|---|
| KON-43 Backend назначение/статус/комментарии | **Pass** (с оговорками по непроверенному, см. п. 6) |
| KON-44 Frontend окно «Назначить» и смена статуса | **Pass с замечаниями** (замечания уже заведены: KON-104, KON-105, KON-106; вёрстка окна отличается от макета) |
| KON-95 Ориентир ставки, API rate-benchmark | **Pass** (NFR-002 по времени отдельно не измерялся) |
| KON-96 status-actions из кэша | **Pass** |
| KON-97 JiraAdapter, обход 11506, ставка | **Pass** (п. 3 «ставка на 801» заменён CR-008; живой обход 11506 не проверялся) |

Новых дефектов не найдено. В Linear ничего не заводил (замечания ниже либо уже заведены, либо нерелевантны задачам).

## 1. Прогон тестов (команда -> результат)

| Команда | Результат |
|---|---|
| `cd backend; python -m pytest -q tests/unit tests/test_jira_integration.py tests/test_health.py` (таймаут 300 с) | **672 passed, 77 skipped**, 0 failed, 12.6 с (skipped — БД-тесты без `TEST_APP_DB_URL`) |
| То же + `TEST_APP_DB_URL`/`TEST_CV_DB_URL` на QA-стенд (127.0.0.1:15432, значения из `qa/stand/.qa.env`): `tests/unit/test_rate_benchmark_db.py test_status_groups_db.py test_sync_db.py` | **28 passed, 1 failed**: `test_sync_db.py::test_status_groups_filter_other_and_counters` — к 5 задачам не относится (счётчики групп статусов на QA-БД; ранее в `qa/test_plan.md` уже был 1 failed в `test_sync_db`). В рамках задач — все 8 DB-тестов `test_rate_benchmark_db.py` зелёные |
| `python -m pytest -q tests/qa/test_kon100_rate_qa.py` | все passed (KON-100 QA: ставка, формат, 1 xfail из KON-103 снят коммитом 1d8dd67) |
| `python -m pytest -q tests/qa` целиком | **не завершился за 300 с** (зависание, известное) — не использован. С исключением `test_stand_api_qa.py`, `test_stand_nfr_qa.py`, `test_ldap_container_qa.py`: **148 passed, 133 skipped, 3 failed** |
| — 3 failed: `test_auth_flow_qa.py::test_tc04_empty_like_password_never_creates_session[]`, `test_tc05_injection_like_logins_are_not_accepted[  ]` и `[]` | ожидали 401, получают 422 (пустой/пробельный логин/пароль отсекается валидацией до проверки). К задачам этапа 2 не относятся (модуль auth), тест QA старше; **нужно решение: поправить ожидание теста (422 допустим) или считать багом auth**. Не заводил |
| `cd frontend; npx vitest run assign needs employees` | **4 файла, 75 passed, 0 failed** (assign 24, needs, employees 18, ещё 1 файл) |
| `npx tsc --noEmit` | чисто |
| `PYTHONPATH=../src` сверка `openapi.json` с `app.openapi()` | совпадает; `/needs/rate-benchmark` есть |
| eslint | не запускал: известный баг KON-105 (правило `react-hooks/exhaustive-deps` не найдено, `StatusActions.tsx`), не блокирует |

## 2. KON-43 — backend назначение/статус/комментарии — Pass

| Критерий / требование | Доказательство |
|---|---|
| Вызов Jira не дольше 10 с (NFR-009) | `backend/app/integrations/jira/adapter.py:15` (повторы выключены, лимит 10 с из `JiraConfig`), `:97`, `:182` передают `timeout`; тесты адаптера в `tests/test_jira_integration.py` зелёные |
| Введённые данные не теряются при отказе и 401 | `service.py:62-70` `AssignError.completed`; тесты `test_assign_failure_keeps_completed_steps_and_retry_does_not_duplicate` (`tests/unit/test_assign.py:384`), `test_api_errors_text_as_is_with_completed_for_retry` (`:469`); 401 -> 409 `needs_reauthorization` (`router.py:28`) |
| Операции под учёткой пользователя (BR-001) | токен пользователя: `service.py` `_token(login)`; `assignee` = логин сессии (`service.py` блок `known`); тест `test_adapter_request_shapes_and_user_token` (`:508`) |
| Приложение не вводит своих ограничений | только белый список FR-026 (`service.py` `ACTIONS`) и проверка обязательных полей до отправки (`_missing`) |
| Пароль нигде не сохраняется / не в логах (NFR-006) | `test_no_secrets_or_comment_text_in_logs` (`:558`); в запросах модуля пароля нет (`router.py:5-8`), входит через `/auth/jira-relogin`; `tests/unit/test_jira_session.py` зелёные |
| Оркестрация назначения (перечитать, профиль, исполнитель, вложения, комментарий, переход, повторное назначение) | `service.py` `assign()`; тесты `test_assign_full_flow_order_and_payload` (`:265`), `test_reassign_from_assigned_status_skips_transition_and_comments` (`:376`), `test_assign_reread_failure_does_not_undo_operation` (`:402`) |
| Смена статуса всегда с комментарием и полями перехода; «Кандидатов нет» с причиной | `service.py` `change_status` (комментарий по умолчанию, проверка причины и комментария до отправки); тесты `:197`, `:210`, `:236` |
| Отказ Jira — текст как есть | `test_api_errors_text_as_is_with_completed_for_retry`, `test_status_refusal_text_as_is_and_reauth` (`:249`) |

Замечание (не дефект): пункт 4 описания («на этапе 1 режим «скопировать комментарий и скачать файлы»») относится к этапу 1, здесь не проверялся.

## 3. KON-97 — JiraAdapter, обход 11506, ставка — Pass

| Пункт | Доказательство |
|---|---|
| 1. Мост `jira_write.py` заменён публичным `JiraPort` | файла `backend/app/assign/jira_write.py` нет (`ls app/assign`: `factory, repo, router, service`); `service.py` использует `self._jira.get_transitions/do_transition/set_fields/add_comment/attach_file`; docstring `service.py:23-24`; обращений к `JiraAdapter._client` вне адаптера нет (grep) |
| 2. Обход 11506 (CR-006) | `service.py:481-` `_transition_assigned`: обычная попытка -> только при 400 с `attachment`/«вложени» (`_is_cv_validator_refusal`) и 11506 = «Да» (`_need_cv_rule_on`) -> «Нет» -> переход -> «Да» в `finally`; неудачный возврат -> `CV_RESTORE_WARNING` в warnings/тексте ошибки; «обход не помог» -> текст Jira + «назначьте вручную». Ссылки на CR-006 и KON-42 в коде есть. Тесты `tests/unit/test_assign.py:613` (успех, порядок), `:625` (не используется без отказа/при выключенном правиле), `:641` (возврат «Да» при ошибке перехода), `:654`, `:664` (неудачный возврат), `:675` (обход не помог), `:687` (API warnings) |
| 3. Ставка | **заменено CR-008 (KON-100), не дефект**: `assign` пишет ставку отдельным `set_rate` после назначения только при «от производства» (`service.py:411, 461-477`); на 801 ставка не передаётся (`service.py:341` пропуск `F_RATE`); тесты `:574`, `:586`, `:592`, `:299`, `:311`, `:322`, `:696`. Живая проверка записи ставки — `qa/KON-101_live_report.md` (Pass, ANALYTICS-5925, возврат 2700 подтверждён; 801 требует только 20810–20812, не 20809) |
| 4. D3: неизвестный id перехода (500) не `jira_unavailable` | `test_unknown_transition_500_is_not_retryable_unavailable` (`:736`) |
| 5. 401/403/503 не изменились после замены моста | `test_service_on_real_adapter_maps_401_403_503` (`:726`) |
| Критерий: `pytest tests/unit tests/test_jira_integration.py tests/test_health.py` зелёные | 672 passed (п. 1) |
| Критерий: пароль/токен не в логах | `:558`, `tests/unit/test_log_filter.py` |
| `openapi.json` перегенерирован | сверка выполнена, совпадает |

Не проверено вживую: сам обход 11506 («Нет» -> переход 21 -> «Да») на живой Jira — в KON-101 не входил (проверялись `set_rate` и GET переходов), подтверждён только на моках; ANALYTICS-5925 уже в статусе «Кандидат назначен». Требует отдельной живой проверки на потребности в подходящем статусе (разрешение владельца).

## 4. KON-95 — ориентир ставки — Pass

| Критерий | Доказательство |
|---|---|
| `GET /needs/rate-benchmark?period_months=6` (по умолчанию 6), ответ `period_months, vat_percent, groups[{currency, unit, count, mean, median}], as_of` | `backend/app/sync/rate_benchmark.py:18-45, 61-77`; `test_defaults_to_six_months_and_vat_22` (`tests/unit/test_rate_benchmark.py:79`) |
| Рубли без НДС приводятся с НДС 22% (BR-009), НДС — настройка `vat_percent` | `repo.py:282-` (SQL `CASE ... rate_amount * (1 + vat/100)`), `rate_benchmark.py:22-23, 49-55`; `test_vat_setting_is_used` (`:111`), `test_parse_vat` (`:123`); DB: `test_vat_conversion_mean_and_median`, `test_vat_percent_is_parameter` — passed на QA-БД; ключ `vat_percent` описан в `db/migrations/app_db/V001__meta_and_core.sql:81` |
| Валюты отдельно, единицы отдельно | DB-тесты `test_currencies_are_separate_and_not_converted`, `test_units_are_separate` — passed |
| Группа < 3 записей: только `count` | `repo.py` `CASE WHEN count(*) >= min`; DB `test_group_under_three_has_count_without_mean` — passed |
| Пустой период; пустая ставка игнорируется; период по `jira_created_at` | `test_empty_period_gives_empty_groups` (`:98`), DB `test_period_filters_by_created_and_empty_rate_is_ignored` |
| 422 на неверный `period_months` | `test_bad_period_is_422` (`:105`); границы `ge=1, le=60` (`rate_benchmark.py:66`; верхняя граница 60 — допущение Dev, спецификацией не задана) |
| Расчёт в SQL (`avg`, `percentile_cont`), без Jira | `repo.py:282-` |
| Авторизация | `test_requires_login` (`:74`) |
| Frontend: ориентир в `AssignDialog`, при ошибке/пусто скрыт | `AssignDialog.tsx:61, 82, 294`; `frontend/src/test/assign.test.tsx:362` (один запрос `?period_months=6`), `:369`, `:388`; vitest passed |
| Секреты и ПДн не выводятся | ответ содержит только агрегаты |
| `openapi.json` | совпадает |

Не проверено: NFR-002 («окно открывается из кэша ≤ 2 с») замером не подтверждён — агрегат по `app.jira_need` без отдельного нагрузочного теста; визуальное соответствие ориентира макету `design/KON-79/` — в KON-100-отчёте указано «ориентир отсутствует», к приёмке KON-95 потребуется скриншот (не делал, стенд не поднимал).

## 5. KON-96 — status-actions из кэша — Pass

| Критерий | Доказательство |
|---|---|
| Открытие списка = 0 обращений к Jira | `frontend/src/components/StatusActions.tsx:12-13, 17-25`: кнопки строятся `statusButtons(need.status)` (`frontend/src/lib/assign.ts:15-24`, матрица `MATRIX` по статусу из кэша) без вызовов API; запрос `api.statusActions` только в `StatusDialog` при клике (`:63-68`). Список потребностей — `backend/app/sync/router.py` читает кэш (Jira только `require_jira_ok` = статус сессии, не вызов Jira); в `sync/rate_benchmark.py`, `sync/router.py` вызовов `JiraPort` нет |
| Тест: список не вызывает Jira, клик — ровно один запрос | `assign.test.tsx:189-202`: «кнопки из белого списка и кэша: список не обращается к Jira, клик — ровно один запрос» (0 запросов к `/status-actions` до клика, 1 после) — passed; `:238` — отсутствие запроса при статусе без кнопок. Backend: `test_status_actions_only_on_click_one_jira_roundtrip` (`tests/unit/test_assign.py:459`) — passed |
| Отказ Jira — текст как есть | `StatusActions.tsx:75-111` (`JiraFailure`), «Действие недоступно» при отсутствии перехода; тест `assign.test.tsx:217` |
| Кнопки совпадают с макетом | `MATRIX` (`assign.ts:15-21`) сверена с `design/KON-79/status_actions.md:16-24`: Требуется кандидат / Поиск кандидата / Кандидатов нет / Найм / Кандидат назначен — совпадают; «Отказ по кандидату» и прочие — кнопок нет (в матрице отсутствуют) |
| Ворота UX (решение владельца по варианту A) | вариант A реализован; согласование владельца — вне QA, в Linear не проверял |

Замеры «100 строк = 0 обращений к Jira» на реальном стенде не делал; подтверждено тестом на мок-списке и чтением кода (запрос только в диалоге).

Остаточный риск (по описанию задачи, принято): кнопка может показываться при нестандартном воркфлоу, а Jira переход не разрешит — диалог показывает «Действие недоступно» (проверено кодом `StatusActions.tsx:100-110`).

## 6. KON-44 — frontend окно «Назначить» и смена статуса — Pass с замечаниями

| Критерий | Доказательство |
|---|---|
| UC-001 от «Сопоставить» до назначения | `assign.test.tsx`: «UC-001: форма -> подтверждение с пояснением про статус -> отправка; список перечитан»; `matching.test.tsx` — зелёные; Playwright-сценарий `qa/e2e/ui_kon100.mjs`, результаты `qa/ui_kon100/results.json` (76/80, 4 FAIL = KON-104) |
| Отказ Jira: текст как есть, введённое сохранено | тест «отказ Jira: текст как есть, данные сохранены, «Повторить» передаёт completed»; для статуса — «Кандидатов нет: ... отказ Jira как есть, введённое сохранено» |
| 401: данные не теряются, пароль не остаётся | тест «сессия Jira истекла: пароль в том же окне, данные сохранены, пароль не остаётся» |
| Окно: «Основное CV» по умолчанию, «Ставка, руб. с НДС», «Комментарий», «Файлы» | `AssignDialog.tsx`; ставка обязательна при «от производства», у «Клиент» не вводится (CR-008) — тесты «от производства», «от клиента», «не от производства» |
| Частичный успех (ставка не записана) | тест «назначено, ставка не записана: окно остаётся с предупреждением, повтор шлёт completed» |
| Закрытие окна во время отправки запрещено | тест «во время отправки закрыть окно нельзя» |

Замечания (все заведены ранее, новых нет): KON-104 (текст подсказки в диалоге «Найм»), KON-105 (eslint + флак `matching.test.tsx` под нагрузкой; в этом прогоне не воспроизвёлся), KON-106 (вёрстка: зазоры между полями ~4px против ~16–20px в макете, «×» с рамкой). Наблюдение O1 из KON-100 (отказ на шаге ставки любого кода, включая 401, превращается в 200 `rate_pending`) остаётся решением владельца/UX.

Внимание: `design/KON-79/decision.md` в рабочем дереве изменён (`git status`: ` M`), не мной (раздел KON-102, статус proposed). Приёмку UI по утверждённому варианту нужно сверять с версией со статусом `approved`; предложения KON-102 пока не утверждены.

## 7. Что не проверено

- Живая Jira: обход 11506 (CR-006) на реальной потребности; шаг ставки после реального назначения в одном сценарии; пользовательский 401 на шаге ставки. Запись ставки через `set_rate` живьём — Pass в KON-101.
- NFR-002 (≤2 с) и NFR-009 (≤10 с) — замерами; подтверждены конфигурацией таймаута и кодом.
- Скриншотная сверка окна «Назначить» с ориентиром KON-95 и обеих тем/ширин после последних правок Dev — не повторялась (последний прогон: `qa/ui_kon100`, до KON-95/96 визуально не обновлялся).
- Стенд `docker compose` не пересобирался; QA-стенд собран из старого кода и фейк Jira не знает ScriptRunner.
- `tests/qa` целиком, `test_stand_api_qa.py`, `test_stand_nfr_qa.py`, `test_ldap_container_qa.py` — не запускались (зависание, нужен стенд).
- eslint (KON-105).
- Решения по двум наблюдениям вне задач: 3 падающих теста `test_auth_flow_qa.py` (422 вместо 401) и `test_sync_db.py::test_status_groups_filter_other_and_counters`.
