-- app_db V013: связи-дубли потребностей и их классификация (FR-014 v1.1, CR-023, KON-165).
-- Расчёт связей выполняется при синхронизации (FR-028, окно 2 недели, порог 15%) и хранится
-- как кэш расчёта; classification ставит пользователь («similar» = Похожая, «duplicate» =
-- Дубль, решение KON-165) и переживает пересинхронизацию и пересчёт.
CREATE TABLE app.need_duplicate (
    need_key       text        NOT NULL REFERENCES app.jira_need (jira_key) ON DELETE CASCADE,
    dup_need_key   text        NOT NULL REFERENCES app.jira_need (jira_key) ON DELETE CASCADE,
    match_percent  smallint    NOT NULL CHECK (match_percent BETWEEN 0 AND 100),
    classification text        NOT NULL DEFAULT 'similar'
                               CHECK (classification IN ('similar', 'duplicate')),
    calculated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (need_key, dup_need_key),
    CONSTRAINT need_duplicate_self_chk CHECK (need_key <> dup_need_key)
);

CREATE INDEX need_duplicate_dup_idx ON app.need_duplicate (dup_need_key);

COMMENT ON TABLE app.need_duplicate IS
    'Связи-дубли потребностей (FR-014 v1.1, CR-023): кэш расчёта при синхронизации + классификация пользователя («Похожая»/«Дубль», KON-165).';
COMMENT ON COLUMN app.need_duplicate.match_percent IS
    'Процент совпадения описания требований к кандидату; 15+ = дубль с высокой вероятностью (ISS-005).';
COMMENT ON COLUMN app.need_duplicate.classification IS
    'Классификация пользователем: similar = Похожая (default), duplicate = Дубль; переживает пересчёт (CR-023).';

INSERT INTO meta.schema_migration (version) VALUES ('V013');
