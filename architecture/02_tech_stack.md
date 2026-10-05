# Технологический стек (подтверждён владельцем 05.10)

| Слой | Выбор | Почему |
|---|---|---|
| Бэкенд | Python 3.12, FastAPI, SQLAlchemy 2, Alembic | быстрая агентская разработка, зрелые библиотеки LDAP/DOCX/LLM |
| Фронтенд | React 18 + TypeScript + Vite, тёмная тема по референсам (FR-038) | типовой стек, хорошо генерируется агентами |
| БД | PostgreSQL 16: две БД `app_db`, `cv_db`, две роли | NFR-007, транзакции, полнотекстовый поиск по CV |
| Кэш | таблицы PostgreSQL, без Redis | 4 пользователя, до 100 потребностей в неделю |
| Фоновые задачи | APScheduler в процессе `worker` + очередь задач в таблице `app_db` | без брокера |
| LDAP | `ldap3` (bind пользователя, пароль не сохраняется) | FR-024 |
| Jira | `httpx`, сессионная cookie (`src/jira_client/`, готов); OAuth 1.0a только запасной | Jira Server 8.13: только REST v2, PAT нет |
| Шифрование | `cryptography`, AES-256-GCM, ключ из env `APP_ENC_KEY` | NFR-006 |
| Извлечение текста CV | `python-docx` (doc: LibreOffice headless) | FR-027, FR-037 |
| Обезличивание | правила (regex) + словари (клиенты из Jira, ФИО сотрудников) + `natasha` NER | FR-023 |
| LLM | OpenAI-совместимый HTTP-клиент, URL и ключ из настроек | FR-030 |
| Тесты | pytest, Playwright (e2e), locust (нагрузка) | QA |
| Развёртывание | Docker Compose на ВМ с Linux, nginx, CI: GitHub Actions сейчас, перенос в корпоративный GitLab клонированием (pipeline описываем переносимо) | on-premise |
| Логи | JSON в stdout, фильтр секретов | NFR-006 п.4 |

Команда: ИИ-агенты, фактически 1-3 исполнителя; профиль MVP, микросервисы не нужны (ADR-001).
