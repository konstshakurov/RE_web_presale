-- app_db V010: комментарии эпиков (клиентов) + кэш LLM-тезисов правил (KON-160).
-- Сырьё хранится как есть (так же, как правила из полей - rules_cache jsonb в jira_epic);
-- тезисы - агрегаты LLM без ПДн. Чистка - вместе с задачами (TTL в worker не нужен:
-- комментарии перезаписываются upsert'ом по comment_id).
CREATE TABLE IF NOT EXISTS app.epic_comment (
    epic_key   text NOT NULL REFERENCES app.jira_epic (epic_key),
    comment_id text NOT NULL,
    author     text,
    updated_at timestamptz,
    body       text NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (epic_key, comment_id)
);

CREATE TABLE IF NOT EXISTS app.epic_rules_cache (
    epic_key      text PRIMARY KEY REFERENCES app.jira_epic (epic_key),
    comments_hash text NOT NULL,
    theses_json   jsonb NOT NULL,
    generated_at  timestamptz NOT NULL DEFAULT now(),
    provider_name text
);

INSERT INTO meta.schema_migration (version) VALUES ('V010');
