-- cv_db V004: права роли cv_rw (ADR-003, NFR-007). Роль не имеет доступа к app_db (CONNECT отозван в bootstrap).
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA cv TO cv_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA cv TO cv_rw;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA cv TO cv_rw;
GRANT EXECUTE ON FUNCTION cv.replace_main_cv(text, text, text, bytea, text, text, text) TO cv_rw;
GRANT EXECUTE ON FUNCTION cv.restore_main_cv(bigint) TO cv_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA cv GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO cv_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA cv GRANT USAGE, SELECT ON SEQUENCES TO cv_rw;
ALTER ROLE cv_rw IN DATABASE cv_db SET search_path = cv, public;
INSERT INTO meta.schema_migration (version) VALUES ('V004');
