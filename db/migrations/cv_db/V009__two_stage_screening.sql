-- cv_db V009: двухступенчатая оценка (KON-38, FR-005, FR-032, FR-023): скрининг по выделенным навыкам и глубокая оценка по тексту CV.
-- 1) cv.match_screen / cv.search_screen: кэш предварительных баллов скрининга по связке «потребность|запрос + основное CV + модель»
--    (как cv.recommendation / cv.search_result). skills_hash - хэш набора навыков на момент оценки: изменение набора = пересчёт.
--    Текст CV и заметка в скрининг не уходят, поэтому правка заметки скрининг не устаревает; смена потребности - устаревает.
-- 2) match_batch / search_batch: stage (screen | deep) и run_id - прогон, который подтверждается и отменяется целиком
--    (один предпросмотр и один confirm на прогон для внешнего провайдера, FR-023). У прежних пакетов stage = deep.
-- 3) match_run / search_run: chain_state - ступень 2 после скрининга: pending (ждёт завершения скрининга),
--    pending_force (то же, глубокая оценка топа без учёта кэша - «Пересчитать»), done (решение принято, повторно не проверяется).
--    У прежних прогонов done: скрининга в них не было.
--    search_run.scope - фильтры запуска (статус, технологии, текстовый фильтр): ступень 2 берёт ту же область поиска.
-- Настройки администратора (размер глубокой оценки, размер пакета скрининга) хранятся в app_db.app.app_setting, схема не меняется.
CREATE TABLE cv.match_screen (
    need_key             text        NOT NULL,                       -- -> app_db.app.jira_need.jira_key (мягкая ссылка, ADR-003)
    cv_file_id           bigint      NOT NULL,
    employee_key         text        NOT NULL,
    cv_kind              text        NOT NULL DEFAULT 'main_cv',     -- для составного FK: только основное CV
    model                text        NOT NULL,
    match_percent        smallint    NOT NULL,
    need_jira_updated_at timestamptz NOT NULL,
    skills_hash          text        NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (need_key, cv_file_id, model),
    CONSTRAINT match_screen_percent_chk CHECK (match_percent BETWEEN 0 AND 100),
    CONSTRAINT match_screen_kind_chk    CHECK (cv_kind = 'main_cv'),
    CONSTRAINT match_screen_need_chk    CHECK (need_key ~ '^[A-Z][A-Z0-9_]*-[0-9]+$'),
    CONSTRAINT match_screen_hash_chk    CHECK (skills_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT match_screen_file_fk     FOREIGN KEY (cv_file_id, employee_key, cv_kind)
        REFERENCES cv.employee_file (id, employee_key, kind) ON DELETE CASCADE
);
CREATE INDEX match_screen_need_idx ON cv.match_screen (need_key);

CREATE TABLE cv.search_screen (
    query_hash    text        NOT NULL,
    cv_file_id    bigint      NOT NULL,
    employee_key  text        NOT NULL,
    cv_kind       text        NOT NULL DEFAULT 'main_cv',
    model         text        NOT NULL,
    match_percent smallint    NOT NULL,
    skills_hash   text        NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (query_hash, cv_file_id, model),
    CONSTRAINT search_screen_percent_chk CHECK (match_percent BETWEEN 0 AND 100),
    CONSTRAINT search_screen_kind_chk    CHECK (cv_kind = 'main_cv'),
    CONSTRAINT search_screen_qhash_chk   CHECK (query_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT search_screen_hash_chk    CHECK (skills_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT search_screen_file_fk     FOREIGN KEY (cv_file_id, employee_key, cv_kind)
        REFERENCES cv.employee_file (id, employee_key, kind) ON DELETE CASCADE
);
CREATE INDEX search_screen_created_idx ON cv.search_screen (created_at);

ALTER TABLE cv.match_run    ADD COLUMN chain_state text NOT NULL DEFAULT 'done',
                            ADD CONSTRAINT match_run_chain_chk CHECK (chain_state IN ('pending', 'pending_force', 'done'));
ALTER TABLE cv.search_run   ADD COLUMN chain_state text NOT NULL DEFAULT 'done',
                            ADD COLUMN scope jsonb NOT NULL DEFAULT '{}'::jsonb,
                            ADD CONSTRAINT search_run_chain_chk CHECK (chain_state IN ('pending', 'pending_force', 'done'));

ALTER TABLE cv.match_batch  ADD COLUMN stage  text NOT NULL DEFAULT 'deep',
                            ADD COLUMN run_id uuid NOT NULL DEFAULT gen_random_uuid(),
                            ADD CONSTRAINT match_batch_stage_chk CHECK (stage IN ('screen', 'deep'));
ALTER TABLE cv.search_batch ADD COLUMN stage  text NOT NULL DEFAULT 'deep',
                            ADD COLUMN run_id uuid NOT NULL DEFAULT gen_random_uuid(),
                            ADD CONSTRAINT search_batch_stage_chk CHECK (stage IN ('screen', 'deep'));
-- run_id задаёт приложение (он же идёт в групповое подтверждение); значение по умолчанию нужно только прежним строкам
ALTER TABLE cv.match_batch  ALTER COLUMN run_id DROP DEFAULT;
ALTER TABLE cv.search_batch ALTER COLUMN run_id DROP DEFAULT;
CREATE INDEX match_batch_run_idx  ON cv.match_batch (run_id);
CREATE INDEX search_batch_runid_idx ON cv.search_batch (run_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON cv.match_screen, cv.search_screen TO cv_rw;
INSERT INTO meta.schema_migration (version) VALUES ('V009');
