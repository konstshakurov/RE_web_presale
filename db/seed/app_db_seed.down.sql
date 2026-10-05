-- Очистка тестовых данных app_db (порядок учитывает внешние ключи). sync_state возвращается в исходное состояние.
BEGIN;
DELETE FROM app.jira_need_tag;
DELETE FROM app.jira_need;
DELETE FROM app.jira_epic;
DELETE FROM app.status_group_status;
DELETE FROM app.status_group;
DELETE FROM app.app_setting;
DELETE FROM app.secret_store;
DELETE FROM app.llm_provider;
DELETE FROM app.app_session;
DELETE FROM app.app_user;
UPDATE app.sync_state SET last_sync_at = NULL, last_success_at = NULL, last_attempt_at = NULL,
                          last_error = NULL, last_error_at = NULL, initial_load_done = false;
COMMIT;
