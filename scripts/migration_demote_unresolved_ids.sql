-- One-off repair: demote public ids that are Active with no master behind them.
--
-- These were produced by resets that ran BEFORE PipelineResetService learned to
-- demote as it detaches. The reset nulled master_hotel_id and left status alone;
-- the following run repaired every id it re-registered, but register_master()
-- only runs for a record that CREATES a master, and a record that routes to
-- MANUAL_REVIEW creates none. Those ids kept claiming to be live properties:
-- counted on the dashboard, silently missing from the master-hotels export
-- (which joins through master_hotel_id), and served as a row of nulls by
-- /masters/<id>.
--
-- The reset fix stops new ones appearing. This repairs the ones already there.
--
-- Nothing is destroyed: the public id, its anchor row and its lifecycle history
-- all survive, the id stays matchable, and promote_if_corroborated() restores
-- Active the moment a second distinct supplier attaches. A reviewer's own
-- confirmation is exempt — that is a human ruling, and a reset preserves those.
--
-- Idempotent: re-running it matches nothing, because the rows it fixes no longer
-- satisfy the WHERE clause.
--
--     docker exec -i hotel_mapping_db_v2 psql -U postgres -d hotel_mapping \
--         < scripts/migration_demote_unresolved_ids.sql

BEGIN;

-- Before: what is about to change, so the output states real numbers.
SELECT count(*) AS will_demote
FROM master_hotel_registry
WHERE status = 'Active'
  AND master_hotel_id IS NULL
  AND confirmed_by IS DISTINCT FROM 'reviewer';

SELECT count(*) AS kept_reviewer_confirmed
FROM master_hotel_registry
WHERE status = 'Active'
  AND master_hotel_id IS NULL
  AND confirmed_by = 'reviewer';

-- The history is the point of the registry, so the repair is recorded against
-- each id rather than applied silently. RETURNING drives the log so that exactly
-- the rows this statement changed are the rows logged: selecting them back
-- afterwards by their new state would also match the ~507 ids that were already
-- unbound Provisional, and record a demotion that never happened to them.
WITH demoted AS (
    UPDATE master_hotel_registry
    SET status         = 'Provisional',
        confirmed_at   = NULL,
        confirmed_by   = NULL,
        confirm_reason = NULL
    WHERE status = 'Active'
      AND master_hotel_id IS NULL
      AND confirmed_by IS DISTINCT FROM 'reviewer'
    RETURNING public_id
)
INSERT INTO master_hotel_lifecycle (public_id, event, related_id, detail)
SELECT public_id,
       'DEMOTED',
       NULL,
       'Backfill: Active with no master behind it after a rebuild; seed record '
       'is in Manual Review. Demoted to Provisional - re-promotes on corroboration.'
FROM demoted;

-- After: the invariant this migration establishes.
SELECT count(*) AS remaining_active_unbound
FROM master_hotel_registry
WHERE status = 'Active' AND master_hotel_id IS NULL;

COMMIT;
