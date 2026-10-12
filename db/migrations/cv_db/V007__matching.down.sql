DROP TABLE cv.match_batch;
DROP TABLE cv.match_run;
ALTER TABLE cv.recommendation DROP COLUMN note_snapshot_at;
DROP TABLE cv.file_skill_state;
DELETE FROM meta.schema_migration WHERE version = 'V007';
