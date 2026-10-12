-- cv_db V010: «чего не хватает до 100%» в результатах глубокой оценки (KON-38/KON-41 CR, FR-005, FR-032).
-- gaps - массив коротких формулировок (до 5, каждая до 200 знаков; границы держит приложение), jsonb.
-- NULL - не рассчитано (оценка получена до V010 либо LLM не вернула перечень): интерфейс показывает подсказку «Пересчитать».
-- '[]' - полное совпадение (100%): недостающего нет. Скрининг (match_screen, search_screen) не меняется.
ALTER TABLE cv.recommendation  ADD COLUMN gaps jsonb,
    ADD CONSTRAINT recommendation_gaps_chk CHECK (gaps IS NULL OR jsonb_typeof(gaps) = 'array');
ALTER TABLE cv.search_result   ADD COLUMN gaps jsonb,
    ADD CONSTRAINT search_result_gaps_chk CHECK (gaps IS NULL OR jsonb_typeof(gaps) = 'array');
-- права cv_rw на таблицы выданы целиком (V004), новая колонка отдельной выдачи не требует
INSERT INTO meta.schema_migration (version) VALUES ('V010');
