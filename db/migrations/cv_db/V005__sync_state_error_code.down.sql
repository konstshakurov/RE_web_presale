ALTER TABLE cv.sync_state DROP CONSTRAINT cv_sync_state_error_code_chk;
ALTER TABLE cv.sync_state DROP COLUMN last_error_code;
DELETE FROM meta.schema_migration WHERE version = 'V005';
