-- cv_db V001: сотрудники (кэш карточек Jira), файлы сотрудников, содержимое файлов, навыки, состояние синхронизации.
-- Источники: ERD-001 (Employee, EmployeeFile), DD-001, FR-004, FR-022, FR-027, FR-033, FR-039, FR-040, ADR-009, NFR-004, NFR-007.
-- Файлы CV хранятся в БД (bytea), лимит 10 МБ (ADR-009). Аудита нет (NFR-005). Автоудаления нет (NFR-004).

CREATE SCHEMA meta;
CREATE TABLE meta.schema_migration (
    version    text        PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE SCHEMA cv;

-- Employee: кэш карточки Jira типа Epic (DD-001). Мастер для ADR-003: сотрудник живёт только в cv_db.
CREATE TABLE cv.employee (
    jira_key        text        PRIMARY KEY,
    full_name       text        NOT NULL,
    status          text        NOT NULL,
    assignable      boolean     NOT NULL,   -- false для 4 статусов, вычисляется при синхронизации
    extra_experience text,                  -- поле Jira «Подробнее об опыте»
    note            text,                   -- свободная заметка, не уходит в Jira (FR-033)
    note_author     text,
    note_updated_at timestamptz,
    synced_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT employee_key_chk  CHECK (jira_key ~ '^[A-Z][A-Z0-9_]*-[0-9]+$'),
    CONSTRAINT employee_name_chk CHECK (length(btrim(full_name)) > 0),
    -- у заметки всегда есть автор и дата последней правки (FR-033)
    CONSTRAINT employee_note_chk CHECK (note IS NULL OR (note_author IS NOT NULL AND note_updated_at IS NOT NULL))
);
-- раздел «Резюме»: фильтр по статусу и допустимости к назначению (FR-002, FR-032)
CREATE INDEX employee_status_idx     ON cv.employee (status);
CREATE INDEX employee_assignable_idx ON cv.employee (assignable) WHERE assignable;

-- Состояние синхронизации сотрудников (раз в час и по кнопке, ADR-004). Штамп «актуально на» = last_success_at.
CREATE TABLE cv.sync_state (
    scope             text        PRIMARY KEY,
    last_sync_at      timestamptz,
    last_success_at   timestamptz,
    last_attempt_at   timestamptz,
    last_error        text,                 -- без токенов, cookie, паролей (NFR-006)
    last_error_at     timestamptz,
    CONSTRAINT cv_sync_state_scope_chk CHECK (scope IN ('employees'))
);
INSERT INTO cv.sync_state (scope) VALUES ('employees');

-- EmployeeFile: метаданные файла (FR-004). Байты - в cv.employee_file_content.
-- Версионность основного CV: прежние версии остаются строками с is_archived = true (FR-039), см. V003.
CREATE TABLE cv.employee_file (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employee_key      text        NOT NULL REFERENCES cv.employee (jira_key),
    kind              text        NOT NULL,
    client_epic_key   text,                 -- только для client_template_cv; -> app_db.app.jira_epic (мягкая ссылка, ADR-003)
    file_name         text        NOT NULL,
    mime_type         text        NOT NULL,
    size_bytes        integer     NOT NULL,
    sha256            bytea       NOT NULL,
    is_archived       boolean     NOT NULL DEFAULT false,
    archived_at       timestamptz,
    extracted_text    text,
    extraction_status text        NOT NULL DEFAULT 'none',
    uploaded_by       text        NOT NULL,
    uploaded_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT employee_file_kind_chk    CHECK (kind IN ('main_cv', 'extra_cv', 'materials', 'client_template_cv')),
    CONSTRAINT employee_file_extr_chk    CHECK (extraction_status IN ('ok', 'error', 'none')),
    CONSTRAINT employee_file_size_chk    CHECK (size_bytes BETWEEN 1 AND 10485760),          -- лимит ADR-009: 10 МБ
    CONSTRAINT employee_file_sha_chk     CHECK (octet_length(sha256) = 32),
    CONSTRAINT employee_file_name_chk    CHECK (length(btrim(file_name)) > 0),
    CONSTRAINT employee_file_client_chk  CHECK ((kind = 'client_template_cv') = (client_epic_key IS NOT NULL)),
    -- архив существует только у основного CV (FR-039)
    CONSTRAINT employee_file_archive_chk CHECK (
        (is_archived AND kind = 'main_cv' AND archived_at IS NOT NULL) OR
        (NOT is_archived AND archived_at IS NULL)),
    -- извлечённый текст только для CV (DD-001)
    CONSTRAINT employee_file_text_chk    CHECK (kind <> 'materials' OR (extracted_text IS NULL AND extraction_status = 'none')),
    CONSTRAINT employee_file_ok_text_chk CHECK (extraction_status <> 'ok' OR extracted_text IS NOT NULL),
    -- ключ для составных FK из cv.recommendation
    CONSTRAINT employee_file_id_emp_kind_uq UNIQUE (id, employee_key, kind)
);
-- FR-004: не более одного актуального основного CV на сотрудника
CREATE UNIQUE INDEX employee_file_one_main_uq ON cv.employee_file (employee_key) WHERE kind = 'main_cv' AND NOT is_archived;
-- FR-017: не более одного CV по шаблону на пару сотрудник/клиент (повтор показывает прежнее)
CREATE UNIQUE INDEX employee_file_client_cv_uq ON cv.employee_file (employee_key, client_epic_key) WHERE kind = 'client_template_cv';
-- столбец «Файлы» раздела «Резюме» и окно «Назначить» (FR-008, FR-022)
CREATE INDEX employee_file_employee_idx ON cv.employee_file (employee_key, kind, is_archived);
-- архив: выборка прежних версий по свежести (FR-039)
CREATE INDEX employee_file_archive_idx ON cv.employee_file (employee_key, archived_at DESC) WHERE is_archived;

-- Содержимое файла: отдельная таблица, чтобы списки и выборки метаданных не тянули байты. 1:1 с employee_file.
CREATE TABLE cv.employee_file_content (
    file_id bigint PRIMARY KEY REFERENCES cv.employee_file (id) ON DELETE CASCADE,
    content bytea  NOT NULL,
    CONSTRAINT employee_file_content_len_chk CHECK (octet_length(content) BETWEEN 1 AND 10485760)
);
COMMENT ON TABLE cv.employee_file_content IS 'Каскад удаления - осознанно: содержимое не имеет смысла без строки метаданных (удаление файла FR-040, вытеснение из архива FR-039).';

-- skills_tags (ERD: JSON) -> навыки основного CV (FR-027). Привязаны к файлу, пересчитываются при замене CV.
CREATE TABLE cv.employee_file_skill (
    file_id bigint NOT NULL REFERENCES cv.employee_file (id) ON DELETE CASCADE,
    tag     text   NOT NULL,
    PRIMARY KEY (file_id, tag),
    CONSTRAINT employee_file_skill_chk CHECK (tag = lower(btrim(tag)) AND length(tag) BETWEEN 1 AND 128)
);
-- фильтр по технологии (FR-032) и предварительный отбор кандидатов (FR-005)
CREATE INDEX employee_file_skill_tag_idx ON cv.employee_file_skill (tag, file_id);

INSERT INTO meta.schema_migration (version) VALUES ('V001');
