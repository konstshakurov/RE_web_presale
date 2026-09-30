# Справочник по фреймворкам

## C4 Model

Иерархическая модель визуализации архитектуры ПО.

### Уровни

| Уровень | Что показывает | Для кого |
|---------|---------------|----------|
| **Context** | Система и окружение | Бизнес, стейкхолдеры |
| **Containers** | Технологический стек | Архитекторы, разработчики |
| **Components** | Внутренняя структура | Разработчики, тимлиды |
| **Code** | Классы, модули | Разработчики |

### Правила
- Progressive elaboration: Context → Containers → Components → Code
- Подписывайте связи: протоколы, данные, частота

### Нотация
- **Человек** — пользователь / роль
- **Скруглённый прямоугольник** — контейнер
- **Прямоугольник** — компонент
- **Цилиндр** — хранилище данных
- **Стрелки** — связи

## ARC42

Шаблон документации архитектуры.

### Структура
1. Введение и цели
2. Контрольные показатели (NFR)
3. Контекст и границы
4. Решение по проектированию
5. Архитектура решения (views)
6. Разработка (CI/CD, структура)
7. Концепция целостности (security, reliability)
8. Ресурсы (hardware, software)
9. Словарь
10. Анализ решений (trade-offs, ADR)

### Когда использовать ARC42
- Крупные системы, регулируемые отрасли, команды > 10

## TOGAF

Корпоративный фреймворк архитектуры.

### ADM (Architecture Development Method)

```
Prelim → A(Requirements) → B(Business) → C(Info Systems) → D(Technology)
→ E(Opportunities) → F(Migration) → G(Implementation) → H(Change) → ReqMgmt
```

### Ключевые артефакты
- **Business Scenarios** — выявление требований
- **Building Blocks** — ABB / SBB
- **Architecture Compliance** — проверка стандартов

### Когда использовать TOGAF
- Enterprise-системы, мульти-командные проекты, регулируемые отрасли, долгосрочные (> 3 лет)

## ADR

Записи архитектурных решений.

### Формат
```markdown
## ADR-NNN: [Название]
**Статус:** [Proposed | Accepted | Deprecated | Superseded]
**Дата:** [YYYY-MM-DD]
### Контекст → Решение → Консеквенции → Альтернативы
```

### Практики
- Одно решение — один ADR
- Фиксируйте решения, не детали реализации
- Относительные ссылки на связанные ADR

## Архитектурные паттерны

### Monolith
Единый artefact, общие данные. Для: малых команд, простых систем.

### Microservices
Независимые сервисы, database-per-service. Для: крупных команд, сложных доменов.

### Event-Driven
Асинхронная коммуникация через события. Для: high-throughput, real-time.

### CQRS
Раздельные модели чтения и записи. Для: complex domains, high-read.

### Layered
Presentation → Application → Domain → Infrastructure. Для: enterprise apps.

### Serverless
FaaS + BaaS. Для: event processing, APIs, sporadic workloads.

## Security Patterns

### Authentication vs Authorization
- **AuthN** — кто вы? (OAuth2, JWT, SAML, LDAP)
- **AuthZ** — что разрешено? (RBAC, ABAC)

### OWASP Top 10
Injection, Broken Auth, Sensitive Data Exposure, XXE, Broken Access Control, Misconfiguration, XSS, Insecure Deserialization, Known Vulnerabilities, Insufficient Logging.

### STRIDE

| Угроза | Пример | Митигация |
|--------|--------|-----------|
| **S**poofing | Stolen credentials | MFA, strong auth |
| **T**ampering | MITM, data modification | Integrity checks, signing |
| **R**epudiation | "Не я отправлял" | Audit logging |
| **I**nformation Disclosure | SQLi, exposed secrets | Encryption, access control |
| **D**enial of Service | DDoS, resource exhaustion | Rate limiting, scaling |
| **E**levation of Privilege | Privilege escalation | Least privilege |

### Architecture Conflicts

| Конфликт | Компромисс |
|----------|------------|
| Микросервисы + малая команда | Модульный монолит → микросервисы при росте |
| Enterprise + MVP | Standard profile, итеративно |
| 99.99% SLA + limited budget | 99.9% → 99.99% при росте |
| Real-time + no async | REST → async |
| Data-intensive + small team | Managed services |
| Multi-region + compliance | Single region → multi-region |
| Self-hosted + small team | Managed services |

## NFR Categories

### Performance
Response time (p50/p95/p99), Throughput (RPS/TPS), Resource utilization.

### Scalability
Vertical vs Horizontal, Auto-scaling, Capacity planning.

### Reliability
Availability (99%/99.9%/99.99%), MTTR/MTBF, Fault tolerance.

### Maintainability
Code complexity, Test coverage, Deployment frequency, Lead time.

### Security
Encryption, Access control, Audit logging, Compliance (GDPR/HIPAA/SOC2).
