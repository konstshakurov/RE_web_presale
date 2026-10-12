-- app_db V008: шлюз LLM (KON-37, FR-023, FR-030, NFR-010, ADR-007).
-- 1) llm_provider.model: OpenAI-совместимому API нужно имя модели; в ERD-001 поля не было.
-- 2) llm_task: очередь асинхронных задач LLM в app_db (02_tech_stack: «очередь задач в таблице app_db»).
--    Подстановки обезличивания (плейсхолдер -> оригинал), исходящий текст и результат лежат только
--    шифртекстом AES-256-GCM (ключ из env, key_id как в secret_store); после завершения payload очищается.
ALTER TABLE app.llm_provider ADD COLUMN model text NOT NULL DEFAULT '';
COMMENT ON COLUMN app.llm_provider.model IS 'Имя модели для chat/completions; пусто - не задано (вызов отклоняется с понятной ошибкой).';

CREATE TABLE app.llm_task (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_login   text        NOT NULL REFERENCES app.app_user (ldap_login),
    purpose       text        NOT NULL,                 -- match | cv_check | chat | cv_template ... (свободный код вызывающего модуля)
    ref           text,                                 -- контекст: ключ потребности, ключ сотрудника
    provider_id   integer     NOT NULL REFERENCES app.llm_provider (id),
    is_external   boolean     NOT NULL,                 -- снимок на момент постановки: обезличивание применялось или нет
    status        text        NOT NULL,
    payload_ct    bytea,                                -- JSON {system, prompt, mapping}; NULL после завершения/отмены
    payload_nonce bytea,
    result_ct     bytea,                                -- ответ LLM с обратной подстановкой
    result_nonce  bytea,
    key_id        text        NOT NULL,
    error_code    text,                                 -- timeout | provider_error | no_key | no_model | anonymization_failed | ...
    error_message text,                                 -- без секретов и без текстов запроса (NFR-006)
    attempts      integer     NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    confirmed_at  timestamptz,
    started_at    timestamptz,
    finished_at   timestamptz,
    CONSTRAINT llm_task_status_chk  CHECK (status IN
        ('awaiting_confirmation', 'queued', 'running', 'done', 'failed', 'timeout', 'cancelled')),
    CONSTRAINT llm_task_purpose_chk CHECK (purpose ~ '^[a-z0-9_]{1,64}$'),
    CONSTRAINT llm_task_nonce_chk   CHECK ((payload_nonce IS NULL OR octet_length(payload_nonce) = 12)
                                       AND (result_nonce IS NULL OR octet_length(result_nonce) = 12)),
    CONSTRAINT llm_task_key_id_chk  CHECK (length(key_id) BETWEEN 1 AND 64)
);
-- очередь worker (SKIP LOCKED) и поиск зависших running
CREATE INDEX llm_task_queue_idx ON app.llm_task (created_at) WHERE status = 'queued';
CREATE INDEX llm_task_running_idx ON app.llm_task (started_at) WHERE status = 'running';
-- «мои задачи»
CREATE INDEX llm_task_owner_idx ON app.llm_task (owner_login, created_at DESC);
COMMENT ON TABLE app.llm_task IS 'Очередь задач LLM. Открытых текстов нет: payload/result шифртекстом, подстановки живут до завершения задачи.';

GRANT SELECT, INSERT, UPDATE, DELETE ON app.llm_task TO app_rw;
INSERT INTO meta.schema_migration (version) VALUES ('V008');
