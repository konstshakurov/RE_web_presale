DROP INDEX cv.search_batch_runid_idx;
DROP INDEX cv.match_batch_run_idx;
ALTER TABLE cv.search_batch DROP COLUMN run_id, DROP COLUMN stage;
ALTER TABLE cv.match_batch  DROP COLUMN run_id, DROP COLUMN stage;
ALTER TABLE cv.search_run   DROP COLUMN scope, DROP COLUMN chain_state;
ALTER TABLE cv.match_run    DROP COLUMN chain_state;
DROP TABLE cv.search_screen;
DROP TABLE cv.match_screen;
DELETE FROM meta.schema_migration WHERE version = 'V009';
