DROP INDEX cv.employee_login_idx;
ALTER TABLE cv.employee DROP CONSTRAINT employee_vacation_plan_chk;
ALTER TABLE cv.employee DROP CONSTRAINT employee_login_chk;
ALTER TABLE cv.employee DROP COLUMN vacation_plan;
ALTER TABLE cv.employee DROP COLUMN jira_login;
DELETE FROM meta.schema_migration WHERE version = 'V006';
