-- ===== app_db =====
CREATE SCHEMA app;

CREATE TABLE app.app_user (
    ldap_login text PRIMARY KEY,
    role text NOT NULL DEFAULT 'user',
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    display_name text,  -- V004 (KON-60): ФИО из LDAP cn, NULL - cn не получен
    CONSTRAINT app_user_login_chk CHECK (ldap_login = lower(ldap_login) AND length(ldap_login) BETWEEN 1 AND 128),
    CONSTRAINT app_user_role_chk CHECK (role IN ('user', 'admin')),
    CONSTRAINT app_user_display_name_chk CHECK (length(display_name) BETWEEN 1 AND 256)
);

CREATE TABLE app.app_session (
    session_hash bytea PRIMARY KEY,
    ldap_login text NOT NULL REFERENCES app.app_user (ldap_login),
    created_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    CONSTRAINT app_session_hash_chk CHECK (octet_length(session_hash) = 32),
    CONSTRAINT app_session_expires_chk CHECK (expires_at > created_at)
);
CREATE INDEX app_session_login_active_idx ON app.app_session (ldap_login) WHERE revoked_at IS NULL;
CREATE INDEX app_session_expires_idx ON app.app_session (expires_at);

CREATE TABLE app.llm_provider (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL,
    url text NOT NULL,
    is_external boolean NOT NULL,
    is_default boolean NOT NULL DEFAULT false,
    timeout_sec integer NOT NULL DEFAULT 120,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT llm_provider_name_uq UNIQUE (name),
    CONSTRAINT llm_provider_url_chk CHECK (url ~ '^https?://'),
    CONSTRAINT llm_provider_timeout_chk CHECK (timeout_sec BETWEEN 1 AND 3600)
);
CREATE UNIQUE INDEX llm_provider_one_default_uq ON app.llm_provider (is_default) WHERE is_default;

CREATE TABLE app.secret_store (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind text NOT NULL,
    owner_login text REFERENCES app.app_user (ldap_login),
    provider_id integer REFERENCES app.llm_provider (id),
    ciphertext bytea NOT NULL,
    nonce bytea NOT NULL,
    key_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT secret_store_kind_chk CHECK (kind IN ('jira_session', 'llm_key')),
    CONSTRAINT secret_store_owner_chk CHECK (
        (kind = 'jira_session' AND owner_login IS NOT NULL AND provider_id IS NULL) OR
        (kind = 'llm_key' AND provider_id IS NOT NULL AND owner_login IS NULL)),
    CONSTRAINT secret_store_nonce_chk CHECK (octet_length(nonce) = 12),
    CONSTRAINT secret_store_cipher_chk CHECK (octet_length(ciphertext) >= 16),
    CONSTRAINT secret_store_key_id_chk CHECK (length(key_id) BETWEEN 1 AND 64)
);
CREATE UNIQUE INDEX secret_store_jira_owner_uq ON app.secret_store (owner_login) WHERE kind = 'jira_session';
CREATE UNIQUE INDEX secret_store_llm_provider_uq ON app.secret_store (provider_id) WHERE kind = 'llm_key';

CREATE TABLE app.app_setting (
    key text PRIMARY KEY,
    value text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT app_setting_key_chk CHECK (key ~ '^[a-z0-9_]{1,64}$')
);

CREATE TABLE app.status_group (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL,
    sort_order integer NOT NULL,
    CONSTRAINT status_group_name_uq UNIQUE (name)
);
CREATE TABLE app.status_group_status (
    group_id integer NOT NULL REFERENCES app.status_group (id) ON DELETE CASCADE,
    status_name text NOT NULL,
    PRIMARY KEY (group_id, status_name),
    CONSTRAINT status_group_status_name_chk CHECK (length(status_name) BETWEEN 1 AND 128)
);
CREATE INDEX status_group_status_status_idx ON app.status_group_status (status_name);

CREATE TABLE app.jira_epic (
    epic_key text PRIMARY KEY,
    name text NOT NULL,
    summary text,
    rules_cache jsonb,
    rules_fetched_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT jira_epic_key_chk CHECK (epic_key ~ '^[A-Z][A-Z0-9_]*-[0-9]+$')
);

CREATE TABLE app.jira_need (
    jira_key text PRIMARY KEY,
    title text NOT NULL,
    description text,
    requirements_text text,
    status text NOT NULL,
    epic_key text REFERENCES app.jira_epic (epic_key),
    employee_key text,
    assignee text,
    author text,
    contract_type text,
    rate_amount numeric(14,2),
    rate_currency text,
    rate_includes_vat boolean,
    rate_unit text,
    rate_proposer text,
    jira_created_at timestamptz NOT NULL,
    jira_updated_at timestamptz NOT NULL,
    synced_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT jira_need_key_chk CHECK (jira_key ~ '^[A-Z][A-Z0-9_]*-[0-9]+$'),
    CONSTRAINT jira_need_contract_chk CHECK (contract_type IS NULL OR contract_type IN ('Прямой контракт', 'Субподрядчик')),
    CONSTRAINT jira_need_currency_chk CHECK (rate_currency IS NULL OR rate_currency IN ('RUB', 'USD', 'KZT')),
    CONSTRAINT jira_need_proposer_chk CHECK (rate_proposer IS NULL OR rate_proposer IN ('от клиента', 'от производства')),
    CONSTRAINT jira_need_rate_chk CHECK (rate_amount IS NULL OR rate_amount >= 0),
    CONSTRAINT jira_need_rate_cur_chk CHECK (rate_amount IS NULL OR rate_currency IS NOT NULL),
    CONSTRAINT jira_need_vat_rub_chk CHECK (rate_amount IS NULL OR rate_currency IS DISTINCT FROM 'RUB' OR rate_includes_vat IS NOT NULL)
);
CREATE INDEX jira_need_status_updated_idx ON app.jira_need (status, jira_updated_at DESC);
CREATE INDEX jira_need_created_idx ON app.jira_need (jira_created_at DESC);
CREATE INDEX jira_need_updated_idx ON app.jira_need (jira_updated_at DESC);
CREATE INDEX jira_need_epic_idx ON app.jira_need (epic_key) WHERE epic_key IS NOT NULL;
CREATE INDEX jira_need_employee_idx ON app.jira_need (employee_key, jira_created_at DESC) WHERE employee_key IS NOT NULL;

CREATE TABLE app.jira_need_tag (
    need_key text NOT NULL REFERENCES app.jira_need (jira_key) ON DELETE CASCADE,
    tag text NOT NULL,
    PRIMARY KEY (need_key, tag),
    CONSTRAINT jira_need_tag_chk CHECK (tag = lower(btrim(tag)) AND length(tag) BETWEEN 1 AND 128)
);
CREATE INDEX jira_need_tag_tag_idx ON app.jira_need_tag (tag);

CREATE TABLE app.sync_state (
    scope text PRIMARY KEY,
    last_sync_at timestamptz,
    last_success_at timestamptz,
    last_attempt_at timestamptz,
    last_error text,
    last_error_at timestamptz,
    initial_load_done boolean NOT NULL DEFAULT false,
    CONSTRAINT sync_state_scope_chk CHECK (scope IN ('needs'))
);
INSERT INTO app.sync_state (scope) VALUES ('needs');

-- ===== cv_db =====
CREATE SCHEMA cv;

CREATE TABLE cv.employee (
    jira_key text PRIMARY KEY,
    full_name text NOT NULL,
    status text NOT NULL,
    assignable boolean NOT NULL,
    extra_experience text,
    note text,
    note_author text,
    note_updated_at timestamptz,
    synced_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT employee_key_chk CHECK (jira_key ~ '^[A-Z][A-Z0-9_]*-[0-9]+$'),
    CONSTRAINT employee_name_chk CHECK (length(btrim(full_name)) > 0),
    CONSTRAINT employee_note_chk CHECK (note IS NULL OR (note_author IS NOT NULL AND note_updated_at IS NOT NULL))
);
CREATE INDEX employee_status_idx ON cv.employee (status);
CREATE INDEX employee_assignable_idx ON cv.employee (assignable) WHERE assignable;

CREATE TABLE cv.sync_state (
    scope text PRIMARY KEY,
    last_sync_at timestamptz,
    last_success_at timestamptz,
    last_attempt_at timestamptz,
    last_error text,
    last_error_at timestamptz,
    CONSTRAINT cv_sync_state_scope_chk CHECK (scope IN ('employees'))
);
INSERT INTO cv.sync_state (scope) VALUES ('employees');

CREATE TABLE cv.employee_file (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employee_key text NOT NULL REFERENCES cv.employee (jira_key),
    kind text NOT NULL,
    client_epic_key text,
    file_name text NOT NULL,
    mime_type text NOT NULL,
    size_bytes integer NOT NULL,
    sha256 bytea NOT NULL,
    is_archived boolean NOT NULL DEFAULT false,
    archived_at timestamptz,
    extracted_text text,
    extraction_status text NOT NULL DEFAULT 'none',
    uploaded_by text NOT NULL,
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT employee_file_kind_chk CHECK (kind IN ('main_cv', 'extra_cv', 'materials', 'client_template_cv')),
    CONSTRAINT employee_file_extr_chk CHECK (extraction_status IN ('ok', 'error', 'none')),
    CONSTRAINT employee_file_size_chk CHECK (size_bytes BETWEEN 1 AND 10485760),
    CONSTRAINT employee_file_sha_chk CHECK (octet_length(sha256) = 32),
    CONSTRAINT employee_file_name_chk CHECK (length(btrim(file_name)) > 0),
    CONSTRAINT employee_file_client_chk CHECK ((kind = 'client_template_cv') = (client_epic_key IS NOT NULL)),
    CONSTRAINT employee_file_archive_chk CHECK (
        (is_archived AND kind = 'main_cv' AND archived_at IS NOT NULL) OR
        (NOT is_archived AND archived_at IS NULL)),
    CONSTRAINT employee_file_text_chk CHECK (kind <> 'materials' OR (extracted_text IS NULL AND extraction_status = 'none')),
    CONSTRAINT employee_file_ok_text_chk CHECK (extraction_status <> 'ok' OR extracted_text IS NOT NULL),
    CONSTRAINT employee_file_id_emp_kind_uq UNIQUE (id, employee_key, kind)
);
CREATE UNIQUE INDEX employee_file_one_main_uq ON cv.employee_file (employee_key) WHERE kind = 'main_cv' AND NOT is_archived;
CREATE UNIQUE INDEX employee_file_client_cv_uq ON cv.employee_file (employee_key, client_epic_key) WHERE kind = 'client_template_cv';
CREATE INDEX employee_file_employee_idx ON cv.employee_file (employee_key, kind, is_archived);
CREATE INDEX employee_file_archive_idx ON cv.employee_file (employee_key, archived_at DESC) WHERE is_archived;

CREATE TABLE cv.employee_file_content (
    file_id bigint PRIMARY KEY REFERENCES cv.employee_file (id) ON DELETE CASCADE,
    content bytea NOT NULL,
    CONSTRAINT employee_file_content_len_chk CHECK (octet_length(content) BETWEEN 1 AND 10485760)
);

CREATE TABLE cv.employee_file_skill (
    file_id bigint NOT NULL REFERENCES cv.employee_file (id) ON DELETE CASCADE,
    tag text NOT NULL,
    PRIMARY KEY (file_id, tag),
    CONSTRAINT employee_file_skill_chk CHECK (tag = lower(btrim(tag)) AND length(tag) BETWEEN 1 AND 128)
);
CREATE INDEX employee_file_skill_tag_idx ON cv.employee_file_skill (tag, file_id);

CREATE TABLE cv.recommendation (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    need_key text NOT NULL,
    employee_key text NOT NULL,
    cv_file_id bigint NOT NULL,
    cv_kind text NOT NULL DEFAULT 'main_cv',
    match_percent smallint NOT NULL,
    rationale text NOT NULL,
    model text NOT NULL,
    need_jira_updated_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT recommendation_percent_chk CHECK (match_percent BETWEEN 0 AND 100),
    CONSTRAINT recommendation_kind_chk CHECK (cv_kind = 'main_cv'),
    CONSTRAINT recommendation_need_chk CHECK (need_key ~ '^[A-Z][A-Z0-9_]*-[0-9]+$'),
    CONSTRAINT recommendation_file_fk FOREIGN KEY (cv_file_id, employee_key, cv_kind)
        REFERENCES cv.employee_file (id, employee_key, kind) ON DELETE CASCADE,
    CONSTRAINT recommendation_employee_fk FOREIGN KEY (employee_key) REFERENCES cv.employee (jira_key),
    CONSTRAINT recommendation_cache_uq UNIQUE (need_key, cv_file_id, model)
);
CREATE INDEX recommendation_need_idx ON cv.recommendation (need_key, match_percent DESC);
CREATE INDEX recommendation_employee_idx ON cv.recommendation (employee_key);
CREATE INDEX recommendation_file_idx ON cv.recommendation (cv_file_id);

CREATE FUNCTION cv.check_main_cv_archive_limit() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    IF NEW.kind = 'main_cv' AND NEW.is_archived THEN
        SELECT count(*) INTO n FROM cv.employee_file
         WHERE employee_key = NEW.employee_key AND kind = 'main_cv' AND is_archived;
        IF n > 3 THEN
            RAISE EXCEPTION 'main CV archive of % holds % versions, limit is 3 (FR-039)', NEW.employee_key, n
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER employee_file_archive_limit_trg
    AFTER INSERT OR UPDATE OF is_archived, kind ON cv.employee_file
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION cv.check_main_cv_archive_limit();

CREATE FUNCTION cv.forbid_delete_current_main_cv() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.kind = 'main_cv' AND NOT OLD.is_archived THEN
        RAISE EXCEPTION 'current main CV (file id %) cannot be deleted, only replaced (FR-040)', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
END
$$;
CREATE TRIGGER employee_file_no_delete_main_trg
    BEFORE DELETE ON cv.employee_file
    FOR EACH ROW EXECUTE FUNCTION cv.forbid_delete_current_main_cv();

CREATE FUNCTION cv.replace_main_cv(
    p_employee_key text, p_file_name text, p_mime_type text, p_content bytea,
    p_uploaded_by text, p_extracted_text text, p_extraction_status text
) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE
    v_new_id bigint;
BEGIN
    PERFORM 1 FROM cv.employee WHERE jira_key = p_employee_key FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'employee % not found', p_employee_key USING ERRCODE = 'foreign_key_violation';
    END IF;
    UPDATE cv.employee_file
       SET is_archived = true, archived_at = clock_timestamp()
     WHERE employee_key = p_employee_key AND kind = 'main_cv' AND NOT is_archived;
    INSERT INTO cv.employee_file (employee_key, kind, file_name, mime_type, size_bytes, sha256,
                                  extracted_text, extraction_status, uploaded_by)
    VALUES (p_employee_key, 'main_cv', p_file_name, p_mime_type, octet_length(p_content), sha256(p_content),
            p_extracted_text, p_extraction_status, p_uploaded_by)
    RETURNING id INTO v_new_id;
    INSERT INTO cv.employee_file_content (file_id, content) VALUES (v_new_id, p_content);
    DELETE FROM cv.employee_file
     WHERE id IN (SELECT id FROM cv.employee_file
                   WHERE employee_key = p_employee_key AND kind = 'main_cv' AND is_archived
                   ORDER BY archived_at DESC, id DESC
                   OFFSET 3);
    RETURN v_new_id;
END
$$;

CREATE FUNCTION cv.restore_main_cv(p_file_id bigint) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_employee text;
BEGIN
    SELECT employee_key INTO v_employee FROM cv.employee_file
     WHERE id = p_file_id AND kind = 'main_cv' AND is_archived;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'file % is not an archived main CV', p_file_id USING ERRCODE = 'no_data_found';
    END IF;
    PERFORM 1 FROM cv.employee WHERE jira_key = v_employee FOR UPDATE;
    UPDATE cv.employee_file
       SET is_archived = true, archived_at = clock_timestamp()
     WHERE employee_key = v_employee AND kind = 'main_cv' AND NOT is_archived;
    UPDATE cv.employee_file SET is_archived = false, archived_at = NULL WHERE id = p_file_id;
END
$$;
