ALTER TABLE cv.search_result  DROP CONSTRAINT search_result_gaps_chk, DROP COLUMN gaps;
ALTER TABLE cv.recommendation DROP CONSTRAINT recommendation_gaps_chk, DROP COLUMN gaps;
DELETE FROM meta.schema_migration WHERE version = 'V010';
