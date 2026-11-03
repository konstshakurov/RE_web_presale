-- app_db V009 down
DROP TABLE IF EXISTS app.cvtpl_template;
DELETE FROM meta.schema_migration WHERE version = 'V009';
