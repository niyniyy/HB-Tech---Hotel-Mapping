-- ============================================================
-- Retroactively apply the provisional rule to masters that already exist.
--
-- NOT run by the migration, and deliberately so. From here on the pipeline
-- registers every new single-supplier master as Provisional, but the masters
-- already in the table were published under the old rule. Demoting them is a
-- policy decision with a visible consequence — they drop out of every export
-- until corroborated or confirmed — so it is yours to make, not the
-- migration's.
--
-- Measured before writing this, on the data currently loaded:
--
--   2767 masters total
--    884 hold records from exactly one supplier   <- these are demoted
--   1883 hold two or more                          <- untouched
--
-- Reversible. To undo:
--
--   UPDATE master_hotel_registry
--   SET status = 'Active', confirmed_at = NULL, confirmed_by = NULL,
--       confirm_reason = NULL
--   WHERE status = 'Provisional' AND confirmed_by IS NULL;
--
-- Run with:
--   docker exec -i hotel_mapping_db_v2 psql -U postgres -d hotel_mapping \
--     < scripts/demote_single_supplier_masters.sql
-- ============================================================

BEGIN;

-- Masters a reviewer already confirmed, deprecated or merged are left alone:
-- a human decision outranks this sweep.
WITH single_supplier AS (
    SELECT r.public_id
    FROM master_hotel_registry r
    WHERE r.status = 'Active'
      AND r.confirmed_at IS NULL
      AND (
          SELECT count(DISTINCT hm.supplier_name)
          FROM hotel_mappings hm
          WHERE hm.master_hotel_id = r.master_hotel_id
      ) < 2
)
UPDATE master_hotel_registry r
SET status = 'Provisional'
FROM single_supplier
WHERE r.public_id = single_supplier.public_id;

INSERT INTO master_hotel_lifecycle (public_id, event, related_id, detail)
SELECT public_id, 'DEMOTED', NULL,
       'Retroactively marked Provisional — only one supplier'
FROM master_hotel_registry
WHERE status = 'Provisional' AND confirmed_at IS NULL;

SELECT status, count(*) AS masters
FROM master_hotel_registry
GROUP BY status
ORDER BY status;

COMMIT;
