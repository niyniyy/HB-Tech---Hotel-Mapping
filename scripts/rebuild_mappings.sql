-- ============================================================
-- Clear every derived mapping so the pipeline can rebuild from scratch.
--
-- Needed after a matching-rule change: rules only govern records at the moment
-- they are processed, so records mapped under the old rules keep the old
-- outcome forever. Rebuilding is how an existing dataset gets the new ones.
--
-- Only DERIVED data is removed. Three things are deliberately kept:
--
--   master_hotel_anchor        Which supplier row seeded which public id. This
--                              is what makes a rebuild reproduce the SAME
--                              HBM- ids rather than minting new ones, so
--                              anything holding an id downstream stays valid.
--
--   master_hotel_registry      The ids themselves, including merge history.
--                              Repointed at new master_hotel_ids as the
--                              pipeline re-registers them.
--
--   master_non_merge_assertion Reviewer decisions that two hotels are
--                              different. These outrank the algorithm and must
--                              survive a rebuild, or the next run re-merges
--                              exactly what a person separated.
--
-- supplier_hotels is never touched — it is the raw import.
--
--   docker exec -i hotel_mapping_db_v2 psql -U postgres -d hotel_mapping \
--     < scripts/rebuild_mappings.sql
-- ============================================================

BEGIN;

-- Derived mappings. hotel_mappings cascades from master_hotels, but is cleared
-- explicitly so the count is visible.
DELETE FROM hotel_mappings;
DELETE FROM manual_review_candidates;

-- Discards are re-derived on the next run; keeping them would double-count.
DELETE FROM flagged_records;

-- Master embeddings belong to masters that are about to disappear.
DELETE FROM hotel_embeddings WHERE master_hotel_id IS NOT NULL;

DELETE FROM master_hotels;

-- Registry rows outlive their masters. Detach them so nothing points at a
-- deleted master_hotel_id, and return them to Provisional: after the rebuild a
-- master is once again a single supplier's claim until the run corroborates it.
-- Reviewer confirmations are preserved.
UPDATE master_hotel_registry
SET master_hotel_id = NULL,
    status = CASE
        WHEN status = 'Merged'      THEN 'Merged'
        WHEN confirmed_by IS NOT NULL
         AND confirmed_by <> 'pipeline' THEN 'Active'
        ELSE 'Provisional'
    END;

-- Everything goes back through the pipeline.
UPDATE hotel_mapping_queue SET status = 'Pending', retry_count = 0;

SELECT 'supplier_hotels (kept)'    AS item, count(*) FROM supplier_hotels
UNION ALL SELECT 'queue pending',          count(*) FROM hotel_mapping_queue WHERE status = 'Pending'
UNION ALL SELECT 'master_hotels',          count(*) FROM master_hotels
UNION ALL SELECT 'hotel_mappings',         count(*) FROM hotel_mappings
UNION ALL SELECT 'anchors (kept)',         count(*) FROM master_hotel_anchor
UNION ALL SELECT 'public ids (kept)',      count(*) FROM master_hotel_registry
UNION ALL SELECT 'reviewer assertions (kept)', count(*) FROM master_non_merge_assertion;

COMMIT;
