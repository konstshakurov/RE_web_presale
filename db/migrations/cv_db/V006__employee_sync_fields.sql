-- cv_db V006: поля карточки сотрудника из Jira для FR-002 и сопоставления с потребностью FR-016 (KON-61).
-- «Описание» FR-002 = extra_experience («Подробнее об опыте» customfield_13504), отдельной колонки нет.
-- jira_login   - «Профиль сотрудника» customfield_10707 (логин, нижний регистр). НЕ customfield_11515: там числовой «ID пользователя».
--                Неуникален намеренно: если две карточки с одним логином, сопоставление считается неоднозначным (NULL).
-- vacation_plan - поле «Отпуск» customfield_10814 (radiobutton: «не указано» / «Планируется в ближайшие
--                3 месяца» / «В ближайшие 3 месяца не планируется») как есть, включая «не указано»;
--                NULL - поле пусто. Даты отпуска (10815/10816) не хранятся. Список значений не фиксируем.
ALTER TABLE cv.employee ADD COLUMN jira_login     text;
ALTER TABLE cv.employee ADD COLUMN vacation_plan  text;
ALTER TABLE cv.employee ADD CONSTRAINT employee_login_chk
    CHECK (jira_login IS NULL OR (jira_login = lower(jira_login) AND length(jira_login) BETWEEN 1 AND 128));
ALTER TABLE cv.employee ADD CONSTRAINT employee_vacation_plan_chk
    CHECK (vacation_plan IS NULL OR length(vacation_plan) BETWEEN 1 AND 128);
-- поиск карточки по логину при сопоставлении потребностей
CREATE INDEX employee_login_idx ON cv.employee (jira_login) WHERE jira_login IS NOT NULL;
INSERT INTO meta.schema_migration (version) VALUES ('V006');
