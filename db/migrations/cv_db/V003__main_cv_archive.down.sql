DROP FUNCTION cv.restore_main_cv(bigint);
DROP FUNCTION cv.replace_main_cv(text, text, text, bytea, text, text, text);
DROP TRIGGER employee_file_no_delete_main_trg ON cv.employee_file;
DROP FUNCTION cv.forbid_delete_current_main_cv();
DROP TRIGGER employee_file_archive_limit_trg ON cv.employee_file;
DROP FUNCTION cv.check_main_cv_archive_limit();
DELETE FROM meta.schema_migration WHERE version = 'V003';
