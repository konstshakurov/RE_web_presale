-- app_db V004: ФИО пользователя из LDAP (cn), KON-60. NULL допустим: LDAP мог не вернуть cn.
-- E-mail (mail) не хранится: в интерфейсе не нужен, решение отложено.
ALTER TABLE app.app_user ADD COLUMN display_name text;
ALTER TABLE app.app_user ADD CONSTRAINT app_user_display_name_chk CHECK (length(display_name) BETWEEN 1 AND 256);
COMMENT ON COLUMN app.app_user.display_name IS 'ФИО из LDAP (cn), обновляется при каждом входе; NULL - cn не получен, в интерфейсе показывается логин.';
INSERT INTO meta.schema_migration (version) VALUES ('V004');
