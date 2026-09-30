# Кэширование, НФТ, логирование и мониторинг

Не добавлять эксплуатационные паттерны механически. Для каждого требования указать причину и применимость. Числовые значения брать только из подтвержденных требований или явно согласованных рекомендаций.

## Оглавление

1. Решение о кэшировании
2. Redis cache-aside
3. HTTP caching
4. Инвалидация и stampede
5. НФТ
6. Логирование
7. Метрики
8. Алерты
9. Аудит и трассировка

## Решение о кэшировании

Для GET/QUERY списка или поиска всегда выполнить анализ, но не всегда добавлять кэш.

Кэш уместен, если:

- одни и те же запросы повторяются;
- чтение дорогое;
- допустима ограниченная устарелость;
- response не уникален для каждого пользователя либо scope можно безопасно разделить;
- ожидаемый hit rate оправдывает сложность.

Кэш может быть неуместен, если:

- данные должны быть строго актуальными, например доступность слота/остаток перед записью;
- запросы почти всегда уникальны;
- response содержит персональные/tenant-specific данные и нет безопасного scope;
- invalidation сложнее и рискованнее, чем чтение;
- нагрузка и latency уже соответствуют требованиям.

Перед добавлением определить: цель, уровень (Redis, browser, CDN/shared proxy), freshness, владельца, security scope и источник истины.

## Redis cache-aside

Для backend Redis описать:

| Решение | Что определить |
|---|---|
| Паттерн | Cache-aside; Redis не является источником истины |
| Содержимое | Полный response body, DTO/read model или отдельные сущности |
| Cache key | API/representation version, tenant/user scope, locale, currency, canonical filters, order, pagination/cursor |
| TTL | Основной TTL, отдельный TTL пустого результата, jitter |
| Ошибки | Обычно 4xx/5xx/429 не кэшировать |
| Personalization | Не смешивать ответы пользователей/организаций; не включать raw JWT |
| Failure mode | Fail-open либо fail-closed с обоснованием |
| Serialization | Версия схемы, UTF-8 JSON, формат decimal/date |
| Consistency | Допустимая устарелость и read-after-write |

Для QUERY cache key вычислять после валидации из canonical request content и значимых metadata. Нормализация должна совпадать с семантикой обработки. Не включать `X-Request-Id`, trace id, секреты и PII целиком.

Если response зависит от token claims, добавить устойчивый scope (например, tenant/role/user id) либо не кэшировать. Никогда не использовать raw token как часть key.

## HTTP caching

Для GET и QUERY можно рассмотреть:

- `Cache-Control` (`public`/`private`, `max-age`, `s-maxage`, `no-store`, `no-cache`);
- `ETag` + `If-None-Match`/`304`;
- `Last-Modified` + `If-Modified-Since`;
- `Vary` по representation metadata;
- CDN/shared cache только для неперсональных ответов.

Если проект использует только Redis backend, явно установить HTTP policy, например `Cache-Control: no-store`, и не добавлять ETag/CDN/browser cache.

Для QUERY учесть, что cache key по RFC 10008 должен включать request content и связанные metadata; не каждая инфраструктура умеет это корректно. Если поддержки нет, использовать внутренний Redis или equivalent resource/GET по отдельному решению.

POST response не считать обычным reusable cache автоматически. Если проект кэширует POST search внутри backend, описать собственный canonical key и не заявлять стандартное shared HTTP caching без проверки инфраструктуры.

## Инвалидация и stampede

Определить события инвалидации после успешного commit:

- create/update/delete/archive/restore;
- изменения связанных справочников и имен, участвующих в response/фильтрах;
- цена, остаток, рейтинг, статус, фото и другие поля результата;
- массовый импорт и изменение схемы representation.

Допустимые стратегии:

- точечное удаление keys;
- versioned namespace/data version;
- tags/sets по сущности;
- короткий TTL как ограничение stale window.

Не выполнять массовый `KEYS`/delete без оценки нагрузки. При versioned namespace старые keys должны стать недостижимы и исчезнуть по TTL.

Для stampede согласовать:

- single-flight/lock на cache key;
- lock TTL;
- уникальный owner token и compare-and-delete;
- короткое ожидание с jitter;
- число повторных cache GET;
- fallback к БД;
- поведение при Redis failure.

Не копировать значения `TTL 30 секунд`, `lock 2 секунды` или другие числа из учебного примера без подтверждения для проекта.

## НФТ

Добавлять только применимые требования и формулировать проверяемо.

### Производительность

- latency p50/p95/p99 и измеряемая точка;
- maximum processing time и поведение при превышении;
- throughput/RPS и concurrency;
- max payload/response size;
- pagination limit;
- DB query target, индексы, N+1, full scan;
- допустимая стоимость `totalCount`.

### Надежность

- availability/SLO;
- dependency timeout;
- retry eligibility, max attempts, exponential backoff + jitter;
- circuit breaker/bulkhead/fallback;
- транзакционность и атомарность;
- идемпотентность и дедупликация;
- поведение при частичном отказе;
- гарантии доставки события/outbox, если асинхронность уже выбрана.

### Безопасность и privacy

- authentication/authorization/ownership;
- TLS и секреты;
- rate limit/anti-abuse;
- input limits и injection protection;
- PII classification, masking, retention;
- audit;
- cache isolation;
- file validation.

### Совместимость

- API version и backward compatibility;
- tolerance к новым response-полям/enum;
- deprecation/sunset policy;
- schema/version для events и cache entries.

Не писать «быстро», «высокая доступность» или «обрабатывать большую нагрузку». Указывать метрику и целевое значение только после подтверждения.

## Логирование

Использовать структурированные логи. Минимальный безопасный состав:

- timestamp;
- service/component;
- HTTP method;
- route template, а не полный URL с чувствительными query;
- requestId/traceId/spanId;
- анонимизированный/технический actorId или tenantId, если допустимо;
- HTTP status и стабильный errorCode;
- duration;
- dependency name, outcome, duration;
- cache hit/miss/bypass без полного key;
- resourceId для успешной mutation, если допустимо.

Не логировать:

- Bearer/JWT, API key, cookie, пароль;
- полные персональные данные;
- свободный поисковый текст целиком, если он может содержать PII;
- raw request/response body по умолчанию;
- signed URL и credentials внешней системы;
- stack trace в клиентском response.

Для чувствительного `query` логировать длину, категорию и hash/усеченное значение по политике. Для cursor/cache key — короткий hash. Для токена не логировать даже «последние 8 символов», если политика явно этого не разрешает.

## Метрики

Использовать low-cardinality labels. Рассмотреть:

- `requests_total` по route/method/status class/errorCode;
- latency histogram и p50/p95/p99;
- active/in-flight requests;
- rate-limited count;
- dependency requests/errors/timeouts/latency;
- DB query duration/pool saturation;
- cache hit/miss/error/latency/invalidation failure;
- idempotency hit/conflict;
- lock contention/slot conflict;
- бизнес-метрики: созданные записи, пустые результаты, успешные удаления.

Не использовать raw URL, userId, query, cursor или UUID как metric label.

## Алерты

Алерт должен иметь сигнал, окно, порог, severity и канал/владельца. Если числа не заданы, перечислить без порогов:

- рост 5xx/error rate;
- превышение latency SLO;
- dependency timeout/unavailable;
- DB/connection pool saturation;
- cache error или резкое падение hit rate;
- invalidation failure;
- рост 409/429, если он означает продуктовую или эксплуатационную проблему;
- ошибки записи/публикации события;
- очередь/DLQ backlog, если брокер используется.

Не переносить учебные пороги вроде «10 ошибок за 5 минут» в production-требование без подтверждения.

## Аудит и трассировка

Для mutation/delete/block/anonymize зафиксировать audit event:

- actor;
- action;
- resource type/id;
- result;
- timestamp;
- reason, если требуется;
- before/after только в разрешенном объеме;
- correlation/trace id.

Передавать `traceparent` во все синхронные downstream-вызовы и согласованный correlation id в сообщения брокера. Не генерировать новый независимый trace на каждом сервисе. Для внешнего provider request id сохранять связь с внутренним trace.
