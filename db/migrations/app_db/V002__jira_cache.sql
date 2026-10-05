-- app_db V002: кэш Jira (потребности, эпики клиентов, состояние синхронизации). FR-028, ADR-004, NFR-002, NFR-008.
-- Кэш не удаляется автоматически (NFR-004); актуальность определяется штампами, а не удалением.

-- Client = эпик клиента (ERD-001 Client). rules_cache хранится как непрозрачный ответ Jira (структура правил не специфицирована, BR-006).
CREATE TABLE app.jira_epic (
    epic_key         text        PRIMARY KEY,
    name             text        NOT NULL,   -- agile/1.0/epic/{key}.name; для «заглушки» равен epic_key
    summary          text,                   -- fields.summary (у эпика два разных имени, _api_notes п.5.3)
    rules_cache      jsonb,
    rules_fetched_at timestamptz,            -- штамп кэша правил, TTL 1 час (FR-028); NULL = эпик известен, но не загружен
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT jira_epic_key_chk CHECK (epic_key ~ '^[A-Z][A-Z0-9_]*-[0-9]+$')
);

-- Need = потребность, задача Jira проекта ANALYTICS (ERD-001 Need, DD-001).
CREATE TABLE app.jira_need (
    jira_key          text          PRIMARY KEY,
    title             text          NOT NULL,                 -- fields.summary
    description       text,                                   -- для поиска дублей (FR-014)
    requirements_text text,                                   -- customfield_10811
    status            text          NOT NULL,                 -- fields.status.name (не резолюция, BR-010)
    epic_key          text          REFERENCES app.jira_epic (epic_key),   -- Epic Link customfield_10101
    employee_key      text,                                   -- «Профиль сотрудника» -> cv_db.cv.employee.jira_key (мягкая ссылка, ADR-003)
    assignee          text,                                   -- логин исполнителя Jira
    author            text,                                   -- логин автора (reporter)
    contract_type     text,
    rate_amount       numeric(14,2),                          -- customfield_20809
    rate_currency     text,                                   -- customfield_20811
    rate_includes_vat boolean,
    rate_unit         text,
    rate_proposer     text,
    jira_created_at   timestamptz   NOT NULL,                 -- fields.created (окно «2 недели» FR-014, «6 месяцев» FR-028)
    jira_updated_at   timestamptz   NOT NULL,                 -- fields.updated (курсор инкрементального JQL)
    synced_at         timestamptz   NOT NULL DEFAULT now(),   -- когда строка последний раз обновлена из Jira
    CONSTRAINT jira_need_key_chk      CHECK (jira_key ~ '^[A-Z][A-Z0-9_]*-[0-9]+$'),
    CONSTRAINT jira_need_contract_chk CHECK (contract_type IS NULL OR contract_type IN ('Прямой контракт', 'Субподрядчик')),
    CONSTRAINT jira_need_currency_chk CHECK (rate_currency IS NULL OR rate_currency IN ('RUB', 'USD', 'KZT')),
    CONSTRAINT jira_need_proposer_chk CHECK (rate_proposer IS NULL OR rate_proposer IN ('от клиента', 'от производства')),
    CONSTRAINT jira_need_rate_chk     CHECK (rate_amount IS NULL OR rate_amount >= 0),
    CONSTRAINT jira_need_rate_cur_chk CHECK (rate_amount IS NULL OR rate_currency IS NOT NULL),
    -- DD-001: «Включает НДС» обязателен для RUB (BR-003)
    CONSTRAINT jira_need_vat_rub_chk  CHECK (rate_amount IS NULL OR rate_currency IS DISTINCT FROM 'RUB' OR rate_includes_vat IS NOT NULL)
);
COMMENT ON COLUMN app.jira_need.employee_key IS 'Поле Jira «Профиль сотрудника». Нет в ERD-001, нужно для FR-008/FR-016 (история отправок). Без FK: сотрудник в cv_db.';
COMMENT ON COLUMN app.jira_need.status IS 'Группа статуса не хранится: статус входит в несколько групп (FR-035), группы вычисляются join-ом через app.status_group_status.';

-- Индексы под реальные запросы:
-- список потребностей по группе статусов с сортировкой по дате (FR-035, NFR-002/008)
CREATE INDEX jira_need_status_updated_idx ON app.jira_need (status, jira_updated_at DESC);
-- общий список «Все статусы» и окно дублей FR-014 (последние 2 недели)
CREATE INDEX jira_need_created_idx        ON app.jira_need (jira_created_at DESC);
-- сортировка по обновлению и инкрементальный опрос
CREATE INDEX jira_need_updated_idx        ON app.jira_need (jira_updated_at DESC);
-- потребности клиента (FR-015)
CREATE INDEX jira_need_epic_idx           ON app.jira_need (epic_key) WHERE epic_key IS NOT NULL;
-- история отправок сотрудника (FR-016)
CREATE INDEX jira_need_employee_idx       ON app.jira_need (employee_key, jira_created_at DESC) WHERE employee_key IS NOT NULL;

-- requirements_tags (ERD: JSON) -> нормализованная таблица технологий и навыков потребности
CREATE TABLE app.jira_need_tag (
    need_key text NOT NULL REFERENCES app.jira_need (jira_key) ON DELETE CASCADE,
    tag      text NOT NULL,
    PRIMARY KEY (need_key, tag),
    CONSTRAINT jira_need_tag_chk CHECK (tag = lower(btrim(tag)) AND length(tag) BETWEEN 1 AND 128)
);
CREATE INDEX jira_need_tag_tag_idx ON app.jira_need_tag (tag);

-- Состояние синхронизации (ADR-004): штамп «актуально на» = last_success_at; курсор JQL = last_sync_at.
-- Для эпиков штамп лежит в app.jira_epic.rules_fetched_at; для сотрудников - cv.sync_state в cv_db.
CREATE TABLE app.sync_state (
    scope             text        PRIMARY KEY,
    last_sync_at      timestamptz,                 -- начало последнего успешного окна опроса (JQL updated >= last_sync_at - 1 мин)
    last_success_at   timestamptz,                 -- штамп «актуально на»
    last_attempt_at   timestamptz,
    last_error        text,                        -- текст ошибки БЕЗ токенов, cookie, паролей (NFR-006)
    last_error_at     timestamptz,
    initial_load_done boolean     NOT NULL DEFAULT false,   -- первичная загрузка за 6 месяцев выполнена
    CONSTRAINT sync_state_scope_chk CHECK (scope IN ('needs'))
);
INSERT INTO app.sync_state (scope) VALUES ('needs');

INSERT INTO meta.schema_migration (version) VALUES ('V002');
