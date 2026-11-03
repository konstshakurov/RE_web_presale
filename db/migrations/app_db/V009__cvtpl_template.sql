-- app_db V009: временное хранение файла-шаблона клиента между шагами диалога
-- «CV по шаблону клиента» (FR-017, KON-80): generate -> задача LLM -> save.
-- Содержимое - файл клиента (ПДн клиента в общем смысле): чистится при save и
-- лениво (старше 7 дней) при следующей постановке; в логи не попадает.
CREATE TABLE IF NOT EXISTS app.cvtpl_template (
    task_id    uuid PRIMARY KEY REFERENCES app.llm_task (id) ON DELETE CASCADE,
    owner_login text NOT NULL,
    file_name  text NOT NULL,
    content    bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO meta.schema_migration (version) VALUES ('V009');
