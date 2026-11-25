-- app_db V013 down
DROP TABLE IF EXISTS app.need_duplicate;
DELETE FROM meta.schema_migration WHERE version = 'V013';
