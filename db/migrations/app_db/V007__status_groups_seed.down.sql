-- Состав групп уходит каскадом (status_group_status.group_id ON DELETE CASCADE).
DELETE FROM app.status_group WHERE name IN ('Без кандидата', 'Кандидат назначен', 'Интервью', 'Закреплён');
DELETE FROM meta.schema_migration WHERE version = 'V007';
