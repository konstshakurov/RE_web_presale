DROP INDEX cv.employee_dept_head_idx;
ALTER TABLE cv.employee DROP COLUMN dept_head_login, DROP COLUMN dept_head_display_name;
DELETE FROM meta.schema_migration WHERE version = 'V011';
