-- cv_db V011: «Руководитель отдела» в карточке сотрудника (CR-012, FR-002, FR-028; проба KON-133).
-- Источник - Jira customfield_14401 (user). dept_head_login - name в нижнем регистре, dept_head_display_name - displayName.
-- NULL в обеих колонках - «Без РО» (поле пусто либо РО-заглушка «Не назначен»; нормализация в приложении).
-- Заполняется синхронизацией сотрудников; у уже загруженных строк NULL до ближайшего обновления карточки.
ALTER TABLE cv.employee ADD COLUMN dept_head_login text, ADD COLUMN dept_head_display_name text;
CREATE INDEX employee_dept_head_idx ON cv.employee (dept_head_login);
-- права cv_rw на таблицу выданы целиком (V004), новые колонки отдельной выдачи не требуют
INSERT INTO meta.schema_migration (version) VALUES ('V011');
