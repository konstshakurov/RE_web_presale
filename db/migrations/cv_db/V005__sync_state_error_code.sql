-- cv_db V005: код причины последнего отказа Jira при синхронизации сотрудников (KON-35, FR-029).
-- Парная к app_db V005: тот же смысл колонки, текст last_error не разбирается.
ALTER TABLE cv.sync_state ADD COLUMN last_error_code text;
ALTER TABLE cv.sync_state ADD CONSTRAINT cv_sync_state_error_code_chk CHECK (last_error_code IS NULL OR length(last_error_code) BETWEEN 1 AND 64);
COMMENT ON COLUMN cv.sync_state.last_error_code IS 'needs_reauthorization | no_permission | jira_unavailable | jira_error | not_enabled | error; NULL - последний опрос успешен.';
INSERT INTO meta.schema_migration (version) VALUES ('V005');
