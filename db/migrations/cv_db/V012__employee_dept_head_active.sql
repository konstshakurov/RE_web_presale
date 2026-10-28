-- cv_db V012: признак активности пользователя Jira у РО (CR-012, решение владельца 28.10.2026).
-- active из user-объекта customfield_14401. NULL - РО нет либо данных нет до ближайшей синхронизации (не скрывать).
ALTER TABLE cv.employee ADD COLUMN dept_head_active boolean;
-- права cv_rw на таблицу выданы целиком (V004), новые колонки отдельной выдачи не требуют
INSERT INTO meta.schema_migration (version) VALUES ('V012');
