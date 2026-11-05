-- app_db V010 down
DROP TABLE IF EXISTS app.epic_rules_cache;
DROP TABLE IF EXISTS app.epic_comment;
DELETE FROM meta.schema_migration WHERE version = 'V010';
