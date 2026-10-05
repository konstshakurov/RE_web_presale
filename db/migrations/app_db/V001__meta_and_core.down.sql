-- Откат app_db V001: удаляет всю схему приложения вместе с данными. Только для разработки.
DROP SCHEMA app CASCADE;
DROP SCHEMA meta CASCADE;
