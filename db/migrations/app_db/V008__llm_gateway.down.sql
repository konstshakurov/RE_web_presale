DROP TABLE app.llm_task;
ALTER TABLE app.llm_provider DROP COLUMN model;
DELETE FROM meta.schema_migration WHERE version = 'V008';
