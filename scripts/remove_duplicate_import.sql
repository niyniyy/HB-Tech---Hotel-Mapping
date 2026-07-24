-- ============================================================
-- Undo a duplicated import.
--
-- Deletes the second and later copies of any supplier record, keeping the
-- earliest (lowest id) of each (supplier_name, supplier_hotel_id, hotel_name).
--
-- Safe by construction: a copy is only deleted if it carries NO mapping. A row
-- that reached a master is never touched, so this cannot orphan a master or
-- lose a reviewer's decision. In the observed case zero of the 8,432 re-imported
-- rows had a mapping — the pipeline had already flagged every one of them as
-- DUPLICATE_SUPPLIER_ROW or SUPPLIER_ID_COLLISION and refused to map them.
--
-- Cascades: hotel_mapping_queue and flagged_records both have ON DELETE CASCADE
-- against supplier_hotels, so the queue rows and the discard entries created by
-- the duplicate import go with them.
--
-- Run the SELECT first — it changes nothing and tells you exactly what the
-- DELETE would remove.
-- ============================================================

-- A plain "keep the lowest id per (supplier, id, name)" rule is WRONG here. It
-- would also delete the duplicates a supplier ships inside its own feed —
-- Sabre has ~200 such lines — and those must stay: the pipeline maps the first
-- and flags the rest, and that flag is what tells the supplier their file has
-- duplicate rows. Deleting them would silently improve the discard report by
-- destroying the evidence behind it.
--
-- So a row is only removed when it duplicates one imported in a DIFFERENT run,
-- detected as a gap of more than an hour. Duplicates within a single import
-- are left exactly as they are.
CREATE OR REPLACE VIEW duplicate_import_rows AS
SELECT s.id, s.supplier_name, s.created_at
FROM supplier_hotels s
WHERE NOT EXISTS (
        SELECT 1 FROM hotel_mappings hm WHERE hm.supplier_hotel_row_id = s.id
      )
  AND EXISTS (
        SELECT 1
        FROM supplier_hotels earlier
        WHERE earlier.supplier_name     = s.supplier_name
          AND earlier.supplier_hotel_id = s.supplier_hotel_id
          AND earlier.hotel_name IS NOT DISTINCT FROM s.hotel_name
          AND earlier.id < s.id
          AND earlier.created_at < s.created_at - INTERVAL '1 hour'
      );

-- ── 1. Dry run: what would go, and what is protected ────────────────────────
SELECT s.supplier_name,
       count(*)                                              AS total_rows_now,
       count(*) FILTER (WHERE d.id IS NOT NULL)               AS would_delete,
       count(*) FILTER (WHERE d.id IS NULL)                   AS would_remain
FROM supplier_hotels s
LEFT JOIN duplicate_import_rows d ON d.id = s.id
GROUP BY s.supplier_name
ORDER BY s.supplier_name;

-- ── 2. The delete. Uncomment to run. ────────────────────────────────────────
--
-- BEGIN;
-- DELETE FROM supplier_hotels WHERE id IN (SELECT id FROM duplicate_import_rows);
-- SELECT 'supplier_hotels' AS table_name, count(*) FROM supplier_hotels
-- UNION ALL SELECT 'flagged_records', count(*) FROM flagged_records
-- UNION ALL SELECT 'queue rows',      count(*) FROM hotel_mapping_queue;
-- COMMIT;
