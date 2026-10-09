ALTER TABLE app.app_user DROP CONSTRAINT app_user_display_name_chk;
ALTER TABLE app.app_user DROP COLUMN display_name;
DELETE FROM meta.schema_migration WHERE version = 'V004';
