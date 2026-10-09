-- app_db V006: логин из поля Jira «Профиль сотрудника» (customfield_10707) в потребности (KON-61, FR-016).
-- Ключ карточки сотрудника по логину определить нельзя без сопоставления: логин хранится как есть,
-- employee_key заполняет sync, только если в cv.employee ровно одна карточка с таким логином
-- (cv.employee.jira_login, cv_db V006). Неоднозначное и ненайденное остаётся NULL.
ALTER TABLE app.jira_need ADD COLUMN employee_login text;
ALTER TABLE app.jira_need ADD CONSTRAINT jira_need_employee_login_chk
    CHECK (employee_login IS NULL OR (employee_login = lower(employee_login) AND length(employee_login) BETWEEN 1 AND 128));
-- сверка employee_key после синхронизации сотрудников
CREATE INDEX jira_need_employee_login_idx ON app.jira_need (employee_login) WHERE employee_login IS NOT NULL;
COMMENT ON COLUMN app.jira_need.employee_login IS 'Логин (name) пользователя из «Профиль сотрудника» customfield_10707, нижний регистр; NULL - поле пусто.';
COMMENT ON COLUMN app.jira_need.employee_key IS 'Ключ карточки сотрудника в cv_db (cv.employee.jira_key), если логин employee_login однозначно найден в cv.employee.jira_login; иначе NULL. Без FK: сотрудник в cv_db.';
INSERT INTO meta.schema_migration (version) VALUES ('V006');
