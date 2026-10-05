-- cv_db V002: кэш рекомендаций LLM (ERD-001 Recommendation, FR-005).
-- Живёт в cv_db: содержит оценку сотрудника по тексту его CV, имеет FK на сотрудника и файл основного CV.
-- Потребность находится в app_db, поэтому need_key - мягкая ссылка (ADR-003: нет межбазовых запросов и FK).

CREATE TABLE cv.recommendation (
    id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    need_key             text        NOT NULL,                       -- -> app_db.app.jira_need.jira_key (мягкая ссылка)
    employee_key         text        NOT NULL,
    cv_file_id           bigint      NOT NULL,
    cv_kind              text        NOT NULL DEFAULT 'main_cv',     -- служебная колонка для составного FK: только основное CV
    match_percent        smallint    NOT NULL,
    rationale            text        NOT NULL,
    model                text        NOT NULL,
    need_jira_updated_at timestamptz NOT NULL,                       -- снимок jira_updated_at потребности: расхождение = пересчёт (FR-005)
    created_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT recommendation_percent_chk CHECK (match_percent BETWEEN 0 AND 100),
    CONSTRAINT recommendation_kind_chk    CHECK (cv_kind = 'main_cv'),
    CONSTRAINT recommendation_need_chk    CHECK (need_key ~ '^[A-Z][A-Z0-9_]*-[0-9]+$'),
    -- файл принадлежит именно этому сотруднику и является основным CV
    CONSTRAINT recommendation_file_fk FOREIGN KEY (cv_file_id, employee_key, cv_kind)
        REFERENCES cv.employee_file (id, employee_key, kind) ON DELETE CASCADE,
    CONSTRAINT recommendation_employee_fk FOREIGN KEY (employee_key) REFERENCES cv.employee (jira_key),
    -- кэш по связке «потребность + основное CV + модель» (FR-005)
    CONSTRAINT recommendation_cache_uq UNIQUE (need_key, cv_file_id, model)
);
COMMENT ON CONSTRAINT recommendation_file_fk ON cv.recommendation IS 'ON DELETE CASCADE: рекомендация по вытесненной из архива версии CV теряет смысл (FR-039).';
-- «Сопоставить»: топ кандидатов по потребности (FR-005, FR-036)
CREATE INDEX recommendation_need_idx     ON cv.recommendation (need_key, match_percent DESC);
-- удаление/замена сотрудника и просмотр по сотруднику
CREATE INDEX recommendation_employee_idx ON cv.recommendation (employee_key);
CREATE INDEX recommendation_file_idx     ON cv.recommendation (cv_file_id);

INSERT INTO meta.schema_migration (version) VALUES ('V002');
