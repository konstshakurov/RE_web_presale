DROP TABLE app.sync_state;
DROP TABLE app.jira_need_tag;
DROP TABLE app.jira_need;
DROP TABLE app.jira_epic;
DELETE FROM meta.schema_migration WHERE version = 'V002';
