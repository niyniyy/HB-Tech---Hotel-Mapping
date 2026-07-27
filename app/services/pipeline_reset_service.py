import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Typed by the caller, not a checkbox. The endpoint is directly callable, so a
# browser confirm() guards nothing — the token has to travel in the request.
CONFIRM_TOKEN = "RESET"


class PipelineResetService:
    """
    Clear every mapping output and re-queue the imported data, so the same
    records can be re-run after a change to the matcher, the weights or the
    normalizer.

    Three rules govern what survives, and they are not interchangeable:

    IMPORT is preserved.       `supplier_hotels` is the source of truth and is
                               never touched; the whole point is to re-run the
                               same input.

    IDENTITY is preserved.     `master_hotel_registry` (public ids) and
                               `master_hotel_anchor` (which supplier row earned
                               which id) are what let HBM-00000153 survive a
                               rebuild. Deleting them would re-mint every public
                               id and break every consumer holding one.

    DECISIONS are preserved.   `master_non_merge_assertion` and
                               `master_merge_decision` are human rulings. A
                               reset re-runs the machine, not the people.

    The dangerous part is the seam between the first and second rules:
    `master_hotel_registry.master_hotel_id` points into a table this operation
    empties, and there is no foreign key to clear it. Left populated it would go
    on referencing ids that the next run reissues to *different* hotels — the
    exact "cached id silently resolves to another property" failure the identity
    service exists to prevent. So the pointer is nulled while the identity it
    belongs to is kept; `register_master()` re-binds it on the next run.

    The status is demoted along with the pointer, for the same reason and with
    the same limit. `Active` asserts that a master exists and was corroborated;
    once the master is gone that assertion has nothing behind it, and it cannot
    be left for the next run to correct because the run only revisits ids whose
    seed record creates a master — a record that lands in review creates none,
    so its id would keep claiming to be a live property forever. A reviewer's
    own confirmation is exempt, per the third rule.

    Sequences are deliberately NOT restarted. `master_hotel_id` is internal and
    disposable by design, so a restart buys nothing and guarantees id reuse.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def preview(self) -> dict:
        """What a reset would clear, so the confirmation can state real numbers."""
        result = await self.session.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM hotel_mappings)           AS mappings,
                  (SELECT count(*) FROM master_hotels)            AS masters,
                  (SELECT count(*) FROM manual_review_candidates) AS review_candidates,
                  (SELECT count(*) FROM hotel_embeddings)         AS embeddings,
                  (SELECT count(*) FROM flagged_records)          AS discarded,
                  (SELECT count(*) FROM supplier_hotels)          AS supplier_records_kept,
                  (SELECT count(*) FROM master_hotel_registry)    AS public_ids_kept,
                  (SELECT count(*) FROM master_hotel_anchor)      AS anchors_kept,
                  (SELECT count(*) FROM master_non_merge_assertion)
                                                                  AS split_decisions_kept;
                """
            )
        )

        return dict(result.mappings().first())

    async def reset(self, actor: str = "reviewer") -> dict:
        """
        Clear mapping output and re-queue every imported record.

        Runs as one statement batch inside the caller's transaction: a partial
        reset — masters gone but the queue still Completed — would leave the
        database in a state no code path expects.
        """
        cleared = await self.preview()

        # TRUNCATE rather than DELETE: at 10 lakh rows the difference is minutes
        # against milliseconds, and CASCADE resolves the ordering for us.
        # RESTART IDENTITY applies only to these tables' own surrogate keys,
        # which nothing outside them references.
        await self.session.execute(
            text(
                """
                TRUNCATE TABLE
                    hotel_mappings,
                    manual_review_candidates,
                    hotel_embeddings,
                    flagged_records,
                    master_hotels
                RESTART IDENTITY CASCADE;
                """
            )
        )

        # The pointer, not the identity. See the class docstring: this is the
        # step whose absence leaves public ids aimed at hotels they do not
        # describe.
        detached = await self.session.execute(
            text(
                """
                UPDATE master_hotel_registry
                SET master_hotel_id = NULL,
                    -- Demote as we detach. 'Active' is a claim about a master
                    -- that this statement is deleting, so leaving it set lets it
                    -- outlive its own evidence. Most rows are repaired on the
                    -- next run, because register_master() rewrites status when it
                    -- reclaims the id — but it only runs for a record that
                    -- CREATES a master, and a record that now routes to review
                    -- creates none. Those ids were left Active pointing at
                    -- nothing: counted as live properties, silently absent from
                    -- the export that joins through master_hotel_id, and served
                    -- as a row of nulls by /masters/<id>.
                    --
                    -- Nothing is lost by demoting: the public id, its anchor and
                    -- its history all survive, and promote_if_corroborated()
                    -- restores Active as soon as a second supplier attaches.
                    status = CASE
                               WHEN status = 'Active'
                                AND confirmed_by IS DISTINCT FROM 'reviewer'
                               THEN 'Provisional'
                               ELSE status
                             END,
                    -- A reviewer's confirmation is a human ruling and survives a
                    -- reset by this service's own contract; the pipeline's
                    -- auto-corroboration is re-derived by the next run, so its
                    -- metadata is cleared with the status it justified rather
                    -- than left describing a master that no longer exists.
                    confirmed_at = CASE WHEN status = 'Active'
                                         AND confirmed_by IS DISTINCT FROM 'reviewer'
                                        THEN NULL ELSE confirmed_at END,
                    confirm_reason = CASE WHEN status = 'Active'
                                           AND confirmed_by IS DISTINCT FROM 'reviewer'
                                          THEN NULL ELSE confirm_reason END,
                    confirmed_by = CASE WHEN status = 'Active'
                                         AND confirmed_by IS DISTINCT FROM 'reviewer'
                                        THEN NULL ELSE confirmed_by END
                WHERE master_hotel_id IS NOT NULL;
                """
            )
        )

        await self.session.execute(
            text(
                """
                UPDATE hotel_mapping_queue
                SET status = 'Pending',
                    retry_count = 0;
                """
            )
        )

        pending = await self.session.execute(
            text("SELECT count(*) FROM hotel_mapping_queue WHERE status = 'Pending';")
        )
        pending_count = pending.scalar()

        await self.session.commit()

        logger.warning(
            "Pipeline reset by %s — cleared %d mappings / %d masters, "
            "detached %d public ids, re-queued %d records",
            actor, cleared["mappings"], cleared["masters"],
            detached.rowcount, pending_count,
        )

        return {
            "message": (
                "Pipeline reset complete. Mapping output cleared; supplier "
                "records, public ids and reviewer decisions retained."
            ),
            "cleared": cleared,
            "public_ids_detached": detached.rowcount,
            "pending_for_reprocessing": pending_count,
        }
