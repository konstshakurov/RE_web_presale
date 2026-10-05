-- cv_db V003: замена основного CV с архивом 3 версий (FR-039) и защита актуального основного CV от удаления (FR-040).
-- Правила держит БД: приложение вызывает функции ниже, а инварианты проверяются триггерами даже при прямых SQL.

-- Не более 3 архивных версий основного CV у сотрудника. Проверка отложена до COMMIT, чтобы функции
-- могли временно проходить через промежуточные состояния внутри одной транзакции.
CREATE FUNCTION cv.check_main_cv_archive_limit() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    IF NEW.kind = 'main_cv' AND NEW.is_archived THEN
        SELECT count(*) INTO n FROM cv.employee_file
         WHERE employee_key = NEW.employee_key AND kind = 'main_cv' AND is_archived;
        IF n > 3 THEN
            RAISE EXCEPTION 'main CV archive of % holds % versions, limit is 3 (FR-039)', NEW.employee_key, n
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NULL;
END
$$;

CREATE CONSTRAINT TRIGGER employee_file_archive_limit_trg
    AFTER INSERT OR UPDATE OF is_archived, kind ON cv.employee_file
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION cv.check_main_cv_archive_limit();

-- Актуальное основное CV напрямую не удаляется, только заменяется (FR-040). Архивные версии и другие файлы удалять можно.
-- Удаление самого сотрудника из БД не предусмотрено (FK без CASCADE), так что исключений нет.
CREATE FUNCTION cv.forbid_delete_current_main_cv() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.kind = 'main_cv' AND NOT OLD.is_archived THEN
        RAISE EXCEPTION 'current main CV (file id %) cannot be deleted, only replaced (FR-040)', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
END
$$;

CREATE TRIGGER employee_file_no_delete_main_trg
    BEFORE DELETE ON cv.employee_file
    FOR EACH ROW EXECUTE FUNCTION cv.forbid_delete_current_main_cv();

-- Замена основного CV (FR-022, FR-039). Возвращает id нового актуального файла.
-- Текст и статус извлечения передаёт приложение (извлечение выполняется при загрузке, FR-027).
CREATE FUNCTION cv.replace_main_cv(
    p_employee_key      text,
    p_file_name         text,
    p_mime_type         text,
    p_content           bytea,
    p_uploaded_by       text,
    p_extracted_text    text,
    p_extraction_status text
) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE
    v_new_id bigint;
BEGIN
    -- сериализуем конкурентные замены у одного сотрудника
    PERFORM 1 FROM cv.employee WHERE jira_key = p_employee_key FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'employee % not found', p_employee_key USING ERRCODE = 'foreign_key_violation';
    END IF;

    UPDATE cv.employee_file
       SET is_archived = true, archived_at = clock_timestamp()
     WHERE employee_key = p_employee_key AND kind = 'main_cv' AND NOT is_archived;

    INSERT INTO cv.employee_file (employee_key, kind, file_name, mime_type, size_bytes, sha256,
                                  extracted_text, extraction_status, uploaded_by)
    VALUES (p_employee_key, 'main_cv', p_file_name, p_mime_type, octet_length(p_content), sha256(p_content),
            p_extracted_text, p_extraction_status, p_uploaded_by)
    RETURNING id INTO v_new_id;

    INSERT INTO cv.employee_file_content (file_id, content) VALUES (v_new_id, p_content);

    -- в архиве остаются 3 последние версии, самая старая удаляется
    DELETE FROM cv.employee_file
     WHERE id IN (SELECT id FROM cv.employee_file
                   WHERE employee_key = p_employee_key AND kind = 'main_cv' AND is_archived
                   ORDER BY archived_at DESC, id DESC
                   OFFSET 3);
    RETURN v_new_id;
END
$$;

-- Восстановление версии из архива (FR-039, критерий 4): она становится актуальной, текущая уходит в архив.
CREATE FUNCTION cv.restore_main_cv(p_file_id bigint) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_employee text;
BEGIN
    SELECT employee_key INTO v_employee FROM cv.employee_file
     WHERE id = p_file_id AND kind = 'main_cv' AND is_archived;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'file % is not an archived main CV', p_file_id USING ERRCODE = 'no_data_found';
    END IF;

    PERFORM 1 FROM cv.employee WHERE jira_key = v_employee FOR UPDATE;

    UPDATE cv.employee_file
       SET is_archived = true, archived_at = clock_timestamp()
     WHERE employee_key = v_employee AND kind = 'main_cv' AND NOT is_archived;

    UPDATE cv.employee_file SET is_archived = false, archived_at = NULL WHERE id = p_file_id;
    -- число архивных версий не растёт: одна ушла из архива, одна пришла
END
$$;

INSERT INTO meta.schema_migration (version) VALUES ('V003');
