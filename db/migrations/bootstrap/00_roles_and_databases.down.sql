-- Откат bootstrap: УДАЛЯЕТ БАЗЫ И ДАННЫЕ. Только для локальной разработки и тестов.
\set ON_ERROR_STOP on
DROP DATABASE IF EXISTS app_db;
DROP DATABASE IF EXISTS cv_db;
DROP ROLE IF EXISTS app_rw;
DROP ROLE IF EXISTS cv_rw;
