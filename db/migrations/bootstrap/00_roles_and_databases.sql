-- Bootstrap (выполняется один раз суперпользователем в служебной БД postgres).
-- Запуск: psql -d postgres -v app_rw_password="..." -v cv_rw_password="..." -f 00_roles_and_databases.sql
-- Пароли передаются только переменными psql в момент запуска; в репозитории секретов нет.
-- ADR-003 / NFR-007: две БД, две роли, каждая роль без права подключения к чужой БД.
\set ON_ERROR_STOP on

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rw') THEN
        CREATE ROLE app_rw LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cv_rw') THEN
        CREATE ROLE cv_rw LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END
$$;

ALTER ROLE app_rw PASSWORD :'app_rw_password';
ALTER ROLE cv_rw  PASSWORD :'cv_rw_password';

SELECT 'CREATE DATABASE app_db ENCODING ''UTF8''' WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'app_db') \gexec
SELECT 'CREATE DATABASE cv_db ENCODING ''UTF8'''  WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'cv_db')  \gexec

REVOKE ALL ON DATABASE app_db FROM PUBLIC;
REVOKE ALL ON DATABASE cv_db  FROM PUBLIC;
GRANT CONNECT ON DATABASE app_db TO app_rw;
GRANT CONNECT ON DATABASE cv_db  TO cv_rw;
