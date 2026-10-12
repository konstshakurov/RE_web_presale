-- cv_db V008: поиск по смыслу в «Резюме» (KON-38, FR-032): прогон, пакеты и кэш оценок.
-- 1) cv.search_run: прогон одного пользователя по одному запросу (запрос как есть + хэш нормализованного);
--    владелец прогона = владелец задач LLM, он же подтверждает отправку внешнему провайдеру (FR-023).
-- 2) cv.search_batch: пакеты кандидатов; одна задача LLM (app_db.app.llm_task, мягкая ссылка, ADR-003) = один пакет.
-- 3) cv.search_result: кэш оценок по связке «запрос + основное CV + модель» (аналог cv.recommendation для потребностей).
--    Общий для пользователей: тот же запрос по тому же файлу и модели LLM повторно не вызывает.
--    note_snapshot_at - снимок employee.note_updated_at (правка заметки делает оценку устаревшей, FR-033).
-- Запросы и оценки хранятся ограниченное время: приложение чистит записи старше 30 суток (SEARCH_RETENTION_DAYS).
CREATE TABLE cv.search_run (
    requested_by text        NOT NULL,
    query_hash   text        NOT NULL,
    query_text   text        NOT NULL,
    provider_id  integer,                    -- -> app_db.app.llm_provider.id (мягкая ссылка)
    model        text        NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (requested_by, query_hash),
    CONSTRAINT search_run_hash_chk  CHECK (query_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT search_run_query_chk CHECK (char_length(query_text) BETWEEN 1 AND 1000)
);
CREATE INDEX search_run_created_idx ON cv.search_run (created_at);

CREATE TABLE cv.search_batch (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    requested_by  text        NOT NULL,
    query_hash    text        NOT NULL,
    task_id       uuid        NOT NULL,
    employee_keys text[]      NOT NULL,
    file_ids      bigint[]    NOT NULL,      -- основное CV каждого кандидата на момент постановки (по порядку employee_keys)
    status        text        NOT NULL DEFAULT 'pending',
    error_code    text,
    error_message text,                      -- без текстов CV и секретов (NFR-006)
    finished_at   timestamptz,
    CONSTRAINT search_batch_run_fk    FOREIGN KEY (requested_by, query_hash)
        REFERENCES cv.search_run (requested_by, query_hash) ON DELETE CASCADE,
    CONSTRAINT search_batch_status_chk CHECK (status IN ('pending', 'done', 'failed')),
    CONSTRAINT search_batch_len_chk    CHECK (cardinality(employee_keys) >= 1
                                          AND cardinality(employee_keys) = cardinality(file_ids)),
    CONSTRAINT search_batch_task_uq    UNIQUE (task_id)
);
CREATE INDEX search_batch_run_idx ON cv.search_batch (requested_by, query_hash);

CREATE TABLE cv.search_result (
    query_hash       text        NOT NULL,
    cv_file_id       bigint      NOT NULL,
    employee_key     text        NOT NULL,
    cv_kind          text        NOT NULL DEFAULT 'main_cv',   -- для составного FK: только основное CV
    model            text        NOT NULL,
    match_percent    smallint    NOT NULL,
    rationale        text        NOT NULL,
    note_snapshot_at timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (query_hash, cv_file_id, model),
    CONSTRAINT search_result_hash_chk    CHECK (query_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT search_result_percent_chk CHECK (match_percent BETWEEN 0 AND 100),
    CONSTRAINT search_result_kind_chk    CHECK (cv_kind = 'main_cv'),
    CONSTRAINT search_result_file_fk     FOREIGN KEY (cv_file_id, employee_key, cv_kind)
        REFERENCES cv.employee_file (id, employee_key, kind) ON DELETE CASCADE
);
CREATE INDEX search_result_employee_idx ON cv.search_result (employee_key);
CREATE INDEX search_result_created_idx  ON cv.search_result (created_at);

GRANT SELECT, INSERT, UPDATE, DELETE ON cv.search_run, cv.search_batch, cv.search_result TO cv_rw;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA cv TO cv_rw;
INSERT INTO meta.schema_migration (version) VALUES ('V008');
