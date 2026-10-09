DROP INDEX app.jira_need_employee_login_idx;
ALTER TABLE app.jira_need DROP CONSTRAINT jira_need_employee_login_chk;
ALTER TABLE app.jira_need DROP COLUMN employee_login;
COMMENT ON COLUMN app.jira_need.employee_key IS 'Поле Jira «Профиль сотрудника». Нет в ERD-001, нужно для FR-008/FR-016 (история отправок). Без FK: сотрудник в cv_db.';
DELETE FROM meta.schema_migration WHERE version = 'V006';
