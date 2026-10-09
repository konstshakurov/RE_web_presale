-- app_db V005: код причины последнего отказа Jira (KON-35, FR-028, FR-029).
-- api и worker - разные процессы: пользователь должен видеть, чем отличается «сессия истекла»
-- (needs_reauthorization), «нет прав» (no_permission) и «Jira недоступна» (jira_unavailable).
-- Разбор по тексту last_error ненадёжен (текст Jira показывается как есть), поэтому отдельная колонка.
ALTER TABLE app.sync_state ADD COLUMN last_error_code text;
ALTER TABLE app.sync_state ADD CONSTRAINT sync_state_error_code_chk CHECK (last_error_code IS NULL OR length(last_error_code) BETWEEN 1 AND 64);
COMMENT ON COLUMN app.sync_state.last_error_code IS 'needs_reauthorization | no_permission | jira_unavailable | jira_error | not_enabled | error; NULL - последний опрос успешен.';
INSERT INTO meta.schema_migration (version) VALUES ('V005');
