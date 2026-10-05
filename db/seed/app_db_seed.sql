-- Обезличенные тестовые данные app_db. Все логины, ключи и названия вымышлены (префиксы TST*).
-- Реальных персональных данных, токенов, паролей и ключей здесь нет; secret_store содержит заведомо фиктивные байты.
-- Применять после миграций app_db. Повторный запуск: сначала seed/app_db_seed.down.sql.
BEGIN;

INSERT INTO app.app_user (ldap_login, role, active) VALUES
    ('test_admin', 'admin', true),
    ('test_user_1', 'user', true),
    ('test_user_2', 'user', true),
    ('test_revoked', 'user', false);

INSERT INTO app.app_session (session_hash, ldap_login, expires_at) VALUES
    (sha256('dummy-session-1'::bytea), 'test_user_1', now() + interval '8 hours');

INSERT INTO app.llm_provider (name, url, is_external, is_default, timeout_sec) VALUES
    ('Qwen (internal)', 'http://llm.internal.example/api/v1', false, true, 120),
    ('External model (test)', 'https://llm.external.example/v1', true, false, 120);

-- Фиктивный «шифртекст»: 32 нулевых байта и nonce из 12 нулевых байт. Это не секрет, а заполнитель для проверки ограничений.
INSERT INTO app.secret_store (kind, owner_login, ciphertext, nonce, key_id) VALUES
    ('jira_session', 'test_user_1', decode(repeat('00', 32), 'hex'), decode(repeat('00', 12), 'hex'), 'test-key-1');
INSERT INTO app.secret_store (kind, provider_id, ciphertext, nonce, key_id)
SELECT 'llm_key', id, decode(repeat('00', 32), 'hex'), decode(repeat('00', 12), 'hex'), 'test-key-1'
  FROM app.llm_provider WHERE name = 'External model (test)';

INSERT INTO app.app_setting (key, value) VALUES
    ('jql_needs', 'project = TSTN AND issuetype = Task ORDER BY updated DESC'),
    ('vat_percent', '20'),
    ('max_file_mb', '10');

-- Группы статусов по умолчанию (FR-035); «Интервью не пройдено» входит в две группы.
INSERT INTO app.status_group (name, sort_order) VALUES
    ('Без кандидата', 1), ('Кандидат назначен', 2), ('Интервью', 3), ('Закреплён', 4);
INSERT INTO app.status_group_status (group_id, status_name)
SELECT g.id, s.status_name
  FROM app.status_group g
  JOIN (VALUES
      ('Без кандидата', 'Требуется кандидат'), ('Без кандидата', 'Поиск кандидата'), ('Без кандидата', 'Найм'),
      ('Без кандидата', 'Отказ по кандидату'), ('Без кандидата', 'Интервью не пройдено'),
      ('Кандидат назначен', 'Кандидат назначен'),
      ('Интервью', 'Подготовка к интервью'), ('Интервью', 'Интервью состоялось'),
      ('Интервью', 'Интервью пройдено'), ('Интервью', 'Интервью не пройдено'),
      ('Закреплён', 'Закреплен за проектом')
  ) AS s (group_name, status_name) ON s.group_name = g.name;

INSERT INTO app.jira_epic (epic_key, name, summary, rules_cache, rules_fetched_at) VALUES
    ('TSTE-1', 'Клиент Альфа (тест)', 'Клиент Альфа (тест) - Выделенная команда', '{"rules": ["тестовое правило 1"]}', now()),
    ('TSTE-2', 'Клиент Бета (тест)', NULL, NULL, NULL);   -- «заглушка»: эпик известен, но ещё не загружен

INSERT INTO app.jira_need (jira_key, title, description, requirements_text, status, epic_key, employee_key,
                           assignee, author, contract_type, rate_amount, rate_currency, rate_includes_vat,
                           rate_unit, rate_proposer, jira_created_at, jira_updated_at) VALUES
    ('TSTN-101', 'Аналитик в проект Альфа', 'Нужен системный аналитик, REST, SQL', 'Опыт от 3 лет, REST, SQL',
     'Требуется кандидат', 'TSTE-1', NULL, NULL, 'test_user_2', 'Прямой контракт', NULL, NULL, NULL, NULL, 'от клиента',
     now() - interval '3 days', now() - interval '1 hour'),
    ('TSTN-102', 'Бизнес-аналитик в проект Бета', 'Нужен бизнес-аналитик, BPMN', 'BPMN, интервью',
     'Кандидат назначен', 'TSTE-2', 'TSTP-1', 'test_user_1', 'test_user_2', 'Субподрядчик', 4500.00, 'RUB', true,
     'час', 'от производства', now() - interval '10 days', now() - interval '2 days'),
    ('TSTN-103', 'Аналитик без эпика', NULL, NULL, 'Отложено', NULL, NULL, NULL, 'test_user_2', NULL, 90.00, 'USD', NULL,
     'час', NULL, now() - interval '40 days', now() - interval '30 days');

INSERT INTO app.jira_need_tag (need_key, tag) VALUES
    ('TSTN-101', 'rest'), ('TSTN-101', 'sql'), ('TSTN-102', 'bpmn');

UPDATE app.sync_state
   SET last_sync_at = now() - interval '5 minutes', last_success_at = now() - interval '5 minutes',
       last_attempt_at = now() - interval '5 minutes', initial_load_done = true
 WHERE scope = 'needs';

COMMIT;
