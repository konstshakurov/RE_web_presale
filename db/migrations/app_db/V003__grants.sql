-- app_db V003: права роли app_rw (ADR-003, NFR-007). Роль не имеет доступа к cv_db (CONNECT отозван в bootstrap).
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA app TO app_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO app_rw;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app TO app_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA app GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA app GRANT USAGE, SELECT ON SEQUENCES TO app_rw;
ALTER ROLE app_rw IN DATABASE app_db SET search_path = app, public;
-- Схема meta (таблица миграций) приложению недоступна.
INSERT INTO meta.schema_migration (version) VALUES ('V003');
