-- cv_db V007: навыки основного CV (состояние извлечения) и прогоны сопоставления (KON-38, FR-027, FR-005, FR-036).
-- 1) cv.file_skill_state: навыки уже есть как cv.employee_file_skill (V001); состояние нужно, чтобы файл без
--    выделенных навыков не пересчитывался бесконечно и чтобы связать задачу LLM с файлом.
--    Привязка к file_id: замена файла = новый file_id = новое извлечение; тот же файл заново не извлекается (FR-027).
-- 2) cv.recommendation.note_snapshot_at: снимок employee.note_updated_at на момент расчёта.
--    Заметка о сотруднике учитывается при сопоставлении (FR-005, FR-033), её правка делает кэш устаревшим.
-- 3) cv.match_run / cv.match_batch: прогон «Сопоставить» по потребности и его пакеты (одна задача LLM = один пакет
--    кандидатов). Потребность в app_db, поэтому need_key - мягкая ссылка (ADR-003); task_id -> app.llm_task.id.
CREATE TABLE cv.file_skill_state (
    file_id       bigint      PRIMARY KEY REFERENCES cv.employee_file (id) ON DELETE CASCADE,
    status        text        NOT NULL,
    task_id       uuid,                      -- -> app_db.app.llm_task.id (мягкая ссылка)
    model         text,
    requested_by  text        NOT NULL,
    error_code    text,
    error_message text,                      -- без текстов CV и секретов (NFR-006)
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    extracted_at  timestamptz,
    CONSTRAINT file_skill_state_status_chk CHECK (status IN ('pending', 'ok', 'failed')),
    CONSTRAINT file_skill_state_ok_chk     CHECK (status <> 'ok' OR extracted_at IS NOT NULL)
);
CREATE INDEX file_skill_state_pending_idx ON cv.file_skill_state (updated_at) WHERE status = 'pending';
CREATE INDEX file_skill_state_task_idx    ON cv.file_skill_state (task_id) WHERE task_id IS NOT NULL;

ALTER TABLE cv.recommendation ADD COLUMN note_snapshot_at timestamptz;

CREATE TABLE cv.match_run (
    need_key             text        PRIMARY KEY,
    requested_by         text        NOT NULL,
    provider_id          integer,                -- -> app_db.app.llm_provider.id (мягкая ссылка)
    model                text        NOT NULL,
    need_jira_updated_at timestamptz NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT match_run_need_chk CHECK (need_key ~ '^[A-Z][A-Z0-9_]*-[0-9]+$')
);

CREATE TABLE cv.match_batch (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    need_key      text        NOT NULL REFERENCES cv.match_run (need_key) ON DELETE CASCADE,
    task_id       uuid        NOT NULL,
    employee_keys text[]      NOT NULL,
    file_ids      bigint[]    NOT NULL,          -- основное CV каждого кандидата на момент постановки (по порядку employee_keys)
    status        text        NOT NULL DEFAULT 'pending',
    error_code    text,
    error_message text,
    finished_at   timestamptz,
    CONSTRAINT match_batch_status_chk CHECK (status IN ('pending', 'done', 'failed')),
    CONSTRAINT match_batch_len_chk    CHECK (cardinality(employee_keys) >= 1
                                         AND cardinality(employee_keys) = cardinality(file_ids)),
    CONSTRAINT match_batch_task_uq    UNIQUE (task_id)
);
CREATE INDEX match_batch_need_idx ON cv.match_batch (need_key);

GRANT SELECT, INSERT, UPDATE, DELETE ON cv.file_skill_state, cv.match_run, cv.match_batch TO cv_rw;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA cv TO cv_rw;
INSERT INTO meta.schema_migration (version) VALUES ('V007');
