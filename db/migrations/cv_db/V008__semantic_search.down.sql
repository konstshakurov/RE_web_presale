DROP TABLE cv.search_result;
DROP TABLE cv.search_batch;
DROP TABLE cv.search_run;
DELETE FROM meta.schema_migration WHERE version = 'V008';
