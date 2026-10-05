-- Обезличенные тестовые данные cv_db. Все ФИО, ключи и тексты вымышлены; содержимое файлов - короткие тестовые строки.
-- Применять после миграций cv_db. Повторный запуск: сначала seed/cv_db_seed.down.sql.
BEGIN;

INSERT INTO cv.employee (jira_key, full_name, status, assignable, extra_experience, note, note_author, note_updated_at) VALUES
    ('TSTP-1', 'Тестовый Сотрудник 01', 'Свободен', true, 'Тестовый опыт: финтех', 'Тестовая заметка: не подключать в проект Гамма', 'test_user_1', now() - interval '1 day'),
    ('TSTP-2', 'Тестовый Сотрудник 02', 'Свободен', true, NULL, NULL, NULL, NULL),
    ('TSTP-3', 'Тестовый Сотрудник 03', 'На проекте', false, NULL, NULL, NULL, NULL),
    ('TSTP-4', 'Тестовый Сотрудник 04', 'Свободен', true, NULL, NULL, NULL, NULL),
    ('TSTP-5', 'Тестовый Сотрудник 05', 'В отпуске', false, NULL, NULL, NULL, NULL);

UPDATE cv.sync_state SET last_sync_at = now() - interval '20 minutes', last_success_at = now() - interval '20 minutes',
                         last_attempt_at = now() - interval '20 minutes'
 WHERE scope = 'employees';

-- Основное CV: у TSTP-1 пять загрузок подряд - в архиве должны остаться 3 версии, актуальна пятая.
SELECT cv.replace_main_cv('TSTP-1', 'cv_test_1_v' || v || '.docx',
                          'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                          convert_to('Тестовое резюме 1, версия ' || v, 'UTF8'), 'test_user_1',
                          'Тестовое резюме 1, версия ' || v, 'ok')
  FROM generate_series(1, 5) AS v;
SELECT cv.replace_main_cv('TSTP-2', 'cv_test_2.docx', 'application/msword',
                          convert_to('Тестовое резюме 2', 'UTF8'), 'test_user_1', 'Тестовое резюме 2', 'ok');
SELECT cv.replace_main_cv('TSTP-3', 'cv_test_3.docx', 'application/msword',
                          convert_to('Тестовое резюме 3', 'UTF8'), 'test_user_2', NULL, 'error');

-- Навыки текущего основного CV (FR-027)
INSERT INTO cv.employee_file_skill (file_id, tag)
SELECT f.id, t.tag
  FROM cv.employee_file f
  JOIN (VALUES ('TSTP-1', 'rest'), ('TSTP-1', 'sql'), ('TSTP-2', 'bpmn'), ('TSTP-2', 'sql')) AS t (emp, tag) ON t.emp = f.employee_key
 WHERE f.kind = 'main_cv' AND NOT f.is_archived;

-- Дополнительные файлы разных типов
INSERT INTO cv.employee_file (employee_key, kind, file_name, mime_type, size_bytes, sha256, extracted_text, extraction_status, uploaded_by)
VALUES ('TSTP-1', 'extra_cv', 'cv_test_1_extra.docx', 'application/msword', 20, sha256('extra cv content'::bytea), 'extra cv content', 'ok', 'test_user_1'),
       ('TSTP-1', 'materials', 'sample_work.pdf', 'application/pdf', 16, sha256('materials body'::bytea), NULL, 'none', 'test_user_1');
INSERT INTO cv.employee_file (employee_key, kind, client_epic_key, file_name, mime_type, size_bytes, sha256, extracted_text, extraction_status, uploaded_by)
VALUES ('TSTP-1', 'client_template_cv', 'TSTE-1', 'cv_alpha_template.docx', 'application/msword', 18, sha256('template cv body'::bytea), 'template cv body', 'ok', 'test_user_1');

INSERT INTO cv.employee_file_content (file_id, content)
SELECT id, convert_to(file_name, 'UTF8') FROM cv.employee_file f
 WHERE NOT EXISTS (SELECT 1 FROM cv.employee_file_content c WHERE c.file_id = f.id);
UPDATE cv.employee_file f SET size_bytes = octet_length((SELECT content FROM cv.employee_file_content c WHERE c.file_id = f.id)),
                              sha256 = sha256((SELECT content FROM cv.employee_file_content c WHERE c.file_id = f.id))
 WHERE f.kind <> 'main_cv';

-- Кэш рекомендаций LLM по актуальному основному CV (FR-005)
INSERT INTO cv.recommendation (need_key, employee_key, cv_file_id, match_percent, rationale, model, need_jira_updated_at)
SELECT 'TSTN-101', f.employee_key, f.id, m.pct, 'Тестовое обоснование оценки', 'test-model', now() - interval '1 hour'
  FROM cv.employee_file f
  JOIN (VALUES ('TSTP-1', 85), ('TSTP-2', 40)) AS m (emp, pct) ON m.emp = f.employee_key
 WHERE f.kind = 'main_cv' AND NOT f.is_archived;

COMMIT;
