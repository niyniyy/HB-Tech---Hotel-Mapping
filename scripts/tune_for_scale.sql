-- ============================================================
-- Post-bulk-load tuning. Run after importing a large batch.
--
-- The IVFFlat index must be sized to the data: lists ~= sqrt(rows).
-- An index built when the table was small silently degrades toward a
-- sequential scan as the table grows, so it is rebuilt here.
-- ============================================================

SET maintenance_work_mem = '512MB';

-- Planner statistics. Skipping this leaves the planner working from stale row
-- counts and it will choose sequential scans over the geo index.
ANALYZE supplier_hotels;
ANALYZE master_hotels;
ANALYZE hotel_mappings;
ANALYZE hotel_mapping_queue;
ANALYZE hotel_embeddings;

-- Rebuild the vector index at a size appropriate to the current row count.
DO $$
DECLARE
    embedding_rows BIGINT;
    target_lists   INT;
BEGIN
    SELECT count(*) INTO embedding_rows FROM hotel_embeddings;

    target_lists := GREATEST(100, LEAST(4000, (sqrt(embedding_rows))::INT));

    EXECUTE 'DROP INDEX IF EXISTS idx_embedding';
    EXECUTE format(
        'CREATE INDEX idx_embedding ON hotel_embeddings '
        'USING ivfflat (embedding vector_cosine_ops) WITH (lists = %s)',
        target_lists
    );

    RAISE NOTICE 'Rebuilt idx_embedding with lists=% for % rows',
        target_lists, embedding_rows;
END $$;

-- Reclaim space and refresh visibility maps after a large mapping run.
VACUUM ANALYZE hotel_mapping_queue;
VACUUM ANALYZE hotel_mappings;
VACUUM ANALYZE master_hotels;
