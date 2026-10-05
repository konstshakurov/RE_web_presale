-- Очистка тестовых данных cv_db. Текущие основные CV напрямую не удаляются (FR-040), поэтому триггер на время очистки отключается.
-- Только для тестовой среды.
BEGIN;
ALTER TABLE cv.employee_file DISABLE TRIGGER employee_file_no_delete_main_trg;
DELETE FROM cv.recommendation;
DELETE FROM cv.employee_file_skill;
DELETE FROM cv.employee_file;          -- content удаляется каскадом
ALTER TABLE cv.employee_file ENABLE TRIGGER employee_file_no_delete_main_trg;
DELETE FROM cv.employee;
UPDATE cv.sync_state SET last_sync_at = NULL, last_success_at = NULL, last_attempt_at = NULL, last_error = NULL, last_error_at = NULL;
COMMIT;
