ALTER TABLE cv.employee DROP COLUMN dept_head_active;
DELETE FROM meta.schema_migration WHERE version = 'V012';
