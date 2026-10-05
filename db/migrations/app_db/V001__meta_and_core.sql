-- app_db V001: служебная таблица миграций, пользователи, сессии, настройки, LLM-провайдеры, хранилище секретов.
-- Источники: ERD-001 (AppUser, LlmProvider, AppSetting, StatusGroup), FR-021, FR-024, FR-030, FR-035, ADR-006, ADR-008, NFR-006.
-- Таблицы аудита нет намеренно (NFR-005, ADR-011).

CREATE SCHEMA meta;
CREATE TABLE meta.schema_migration (
    version    text        PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE SCHEMA app;

-- AppUser: список допущенных LDAP-аккаунтов (FR-021). Пароль не хранится нигде (ADR-005/006).
CREATE TABLE app.app_user (
    ldap_login text        PRIMARY KEY,
    role       text        NOT NULL DEFAULT 'user',
    active     boolean     NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT app_user_login_chk CHECK (ldap_login = lower(ldap_login) AND length(ldap_login) BETWEEN 1 AND 128),
    CONSTRAINT app_user_role_chk  CHECK (role IN ('user', 'admin'))
);
COMMENT ON TABLE app.app_user IS 'ERD-001 AppUser. Пользователь не удаляется физически, доступ отзывается active=false (FR-021, NFR-004).';

-- Серверные сессии приложения (ADR-008). Хранится только SHA-256 идентификатора сессии, не сам cookie.
CREATE TABLE app.app_session (
    session_hash bytea       PRIMARY KEY,
    ldap_login   text        NOT NULL REFERENCES app.app_user (ldap_login),
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    revoked_at   timestamptz,
    CONSTRAINT app_session_hash_chk    CHECK (octet_length(session_hash) = 32),
    CONSTRAINT app_session_expires_chk CHECK (expires_at > created_at)
);
CREATE INDEX app_session_login_active_idx ON app.app_session (ldap_login) WHERE revoked_at IS NULL;
CREATE INDEX app_session_expires_idx      ON app.app_session (expires_at);

-- LlmProvider (FR-030, FR-023). API-ключ хранится не здесь, а в app.secret_store (kind = 'llm_key').
CREATE TABLE app.llm_provider (
    id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        text        NOT NULL,
    url         text        NOT NULL,
    is_external boolean     NOT NULL,
    is_default  boolean     NOT NULL DEFAULT false,
    timeout_sec integer     NOT NULL DEFAULT 120,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT llm_provider_name_uq     UNIQUE (name),
    CONSTRAINT llm_provider_url_chk     CHECK (url ~ '^https?://'),
    CONSTRAINT llm_provider_timeout_chk CHECK (timeout_sec BETWEEN 1 AND 3600)
);
-- Не более одного провайдера по умолчанию (корпоративная Qwen).
CREATE UNIQUE INDEX llm_provider_one_default_uq ON app.llm_provider (is_default) WHERE is_default;

-- Хранилище зашифрованных секретов (ADR-006, NFR-006): AES-256-GCM, ключ в окружении, key_id для ротации.
-- Заменяет ERD-001 AppUser.jira_token_encrypted и LlmProvider.api_key_encrypted.
CREATE TABLE app.secret_store (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind        text        NOT NULL,
    owner_login text        REFERENCES app.app_user (ldap_login),
    provider_id integer     REFERENCES app.llm_provider (id),
    ciphertext  bytea       NOT NULL,
    nonce       bytea       NOT NULL,
    key_id      text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT secret_store_kind_chk  CHECK (kind IN ('jira_session', 'llm_key')),
    CONSTRAINT secret_store_owner_chk CHECK (
        (kind = 'jira_session' AND owner_login IS NOT NULL AND provider_id IS NULL) OR
        (kind = 'llm_key'      AND provider_id IS NOT NULL AND owner_login IS NULL)),
    CONSTRAINT secret_store_nonce_chk  CHECK (octet_length(nonce) = 12),
    CONSTRAINT secret_store_cipher_chk CHECK (octet_length(ciphertext) >= 16),   -- минимум: тег GCM 16 байт
    CONSTRAINT secret_store_key_id_chk CHECK (length(key_id) BETWEEN 1 AND 64)
);
-- Один сессионный токен Jira на пользователя, один ключ на провайдера.
CREATE UNIQUE INDEX secret_store_jira_owner_uq   ON app.secret_store (owner_login) WHERE kind = 'jira_session';
CREATE UNIQUE INDEX secret_store_llm_provider_uq ON app.secret_store (provider_id) WHERE kind = 'llm_key';
COMMENT ON TABLE app.secret_store IS 'Только шифртекст AES-256-GCM (ciphertext включает тег). Пароль Jira/LDAP не хранится нигде. created_at записи jira_session = момент получения токена.';

-- AppSetting: jql_needs, vat_percent, max_file_mb и др.
CREATE TABLE app.app_setting (
    key        text        PRIMARY KEY,
    value      text        NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT app_setting_key_chk CHECK (key ~ '^[a-z0-9_]{1,64}$')
);

-- StatusGroup (FR-035). Состав групп нормализован: статус может входить в несколько групп.
CREATE TABLE app.status_group (
    id         integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name       text    NOT NULL,
    sort_order integer NOT NULL,
    CONSTRAINT status_group_name_uq UNIQUE (name)
);
CREATE TABLE app.status_group_status (
    group_id    integer NOT NULL REFERENCES app.status_group (id) ON DELETE CASCADE,
    status_name text    NOT NULL,
    PRIMARY KEY (group_id, status_name),
    CONSTRAINT status_group_status_name_chk CHECK (length(status_name) BETWEEN 1 AND 128)
);
CREATE INDEX status_group_status_status_idx ON app.status_group_status (status_name);

INSERT INTO meta.schema_migration (version) VALUES ('V001');
