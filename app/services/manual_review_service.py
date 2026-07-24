import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.hotel_mapping_service import HotelMappingService
from sqlalchemy.exc import IntegrityError
from typing import Any
from app.matching.master_embedding_service import MasterEmbeddingService
from app.services.master_identity_service import MasterIdentityService

logger = logging.getLogger(__name__)

# A batch is a reviewer acting on what is on their screen, so it is bounded by
# what a screen can hold. A larger number would mean deciding about records
# nobody looked at.
BATCH_DECISION_LIMIT = 250


class ManualReviewService:

    def __init__(self, session: AsyncSession):
        self.session = session
        self.mapping_service = HotelMappingService(session)
        self.master_embedding_service = MasterEmbeddingService(session)
        self.identity_service = MasterIdentityService(session)
    async def get_manual_reviews(
        self,
        limit: int = 100
) -> list[dict[str, Any]]:
        result = await self.session.execute(
    text(
        """
        SELECT
    q.supplier_hotel_id,

    s.supplier_name,
    s.supplier_hotel_id AS supplier_hotel_code,
    s.hotel_name,
    s.normalized_name,
    s.address,
    s.city,
    s.state,
    s.country,
    s.postal_code,
    s.star_rating,
    s.latitude,
    s.longitude,

    c.suggested_master_hotel_id,

    m.hotel_name AS master_hotel_name,
    m.normalized_name AS master_normalized_name,
    m.address AS master_address,
    m.city AS master_city,
    m.state AS master_state,
    m.country AS master_country,
    m.postal_code AS master_postal_code,
    m.star_rating AS master_star_rating,
    m.latitude AS master_latitude,
    m.longitude AS master_longitude,

    c.rule_score,
    c.ai_similarity,
    c.decision_reason,
    c.created_at

        FROM hotel_mapping_queue q

        JOIN manual_review_candidates c
          ON q.supplier_hotel_id = c.supplier_hotel_id

        JOIN supplier_hotels s
          ON s.id = q.supplier_hotel_id

        JOIN master_hotels m
          ON m.master_hotel_id = c.suggested_master_hotel_id

        WHERE q.status = 'ManualReview'

        ORDER BY c.created_at DESC

        LIMIT :limit;
        """
    ),
    {"limit": limit}
)

        return [dict(row) for row in result.mappings().all()]
      
    async def get_manual_review(
        self,
        supplier_hotel_id: int
    ) -> dict[str, Any] | None:

        result = await self.session.execute(
            text(
                """
                SELECT
                    q.supplier_hotel_id,

                    s.supplier_name,
                    s.supplier_hotel_id AS supplier_hotel_code,
                    s.hotel_name,
                    s.normalized_name,
                    s.address,
                    s.city,
                    s.state,
                    s.country,
                    s.postal_code,
                    s.star_rating,
                    s.latitude,
                    s.longitude,

                    c.suggested_master_hotel_id,

                    m.hotel_name AS master_hotel_name,
                    m.normalized_name AS master_normalized_name,
                    m.address AS master_address,
                    m.city AS master_city,
                    m.state AS master_state,
                    m.country AS master_country,
                    m.postal_code AS master_postal_code,
                    m.star_rating AS master_star_rating,
                    m.latitude AS master_latitude,
                    m.longitude AS master_longitude,

                    c.rule_score,
                    c.ai_similarity,
                    c.decision_reason,
                    c.created_at

                FROM hotel_mapping_queue q

                JOIN manual_review_candidates c
                    ON q.supplier_hotel_id = c.supplier_hotel_id

                JOIN supplier_hotels s
                    ON s.id = q.supplier_hotel_id

                JOIN master_hotels m
                    ON m.master_hotel_id = c.suggested_master_hotel_id

                WHERE
                    q.status = 'ManualReview'
                    AND q.supplier_hotel_id = :supplier_hotel_id;
                """
            ),
            {
                "supplier_hotel_id": supplier_hotel_id
            }
        )

        row = result.mappings().first()

        if row is None:
            return None

        return dict(row)
      
    async def get_review_record(
    self,
    supplier_hotel_id: int
) -> dict[str, Any] | None:

        result = await self.session.execute(
            text(
                """
                SELECT

                    c.suggested_master_hotel_id,
                    c.rule_score,

                    -- The supplier ROW id, not the supplier's own hotel code.
                    -- insert_hotel_mapping reads `id` for supplier_hotel_row_id
                    -- and this query did not select it, so every approval wrote
                    -- a mapping with a NULL row link: present in the table but
                    -- invisible to the accuracy evaluation, the integrity
                    -- monitor, duplicate detection and the master detail
                    -- screen, all of which join on it.
                    s.id,

                    s.supplier_name,
                    s.supplier_hotel_id

                FROM manual_review_candidates c

                JOIN supplier_hotels s
                  ON s.id = c.supplier_hotel_id

                WHERE c.supplier_hotel_id = :supplier_hotel_id;
                """
            ),
            {
                "supplier_hotel_id": supplier_hotel_id
            }
        )

        row = result.mappings().first()

        if row is None:
            return None

        return dict(row)
      
    async def get_supplier_hotel(
    self,
    supplier_hotel_id: int
) -> dict[str, Any] | None:
        
        result = await self.session.execute(
            text(
                """
                SELECT *
                FROM supplier_hotels
                WHERE id = :supplier_hotel_id;
                """
            ),
            {
                "supplier_hotel_id": supplier_hotel_id
            }
        )

        row = result.mappings().first()

        if row is None:
            return None

        return dict(row)
    
    async def mapping_exists(
    self,
    supplier_name: str,
    supplier_hotel_id: str
) -> bool:
        result = await self.session.execute(
            text(
                """
                SELECT 1
                FROM hotel_mappings
                WHERE supplier_name = :supplier_name
                  AND supplier_hotel_id = :supplier_hotel_id;
                """
            ),
            {
                "supplier_name": supplier_name,
                "supplier_hotel_id": supplier_hotel_id
            }
        )

        return result.scalar() is not None 
      
    async def approve_match(
    self,
    supplier_hotel_id: int
) -> bool:

      review = await self.get_review_record(
          supplier_hotel_id
      )
      
      
      if review is None:
          return False
        
  
      already_exists = await self.mapping_exists(
          review["supplier_name"],
          review["supplier_hotel_id"]
        )
      if already_exists:
          return False

      try:

        await self.mapping_service.insert_hotel_mapping(
              master_hotel_id=review["suggested_master_hotel_id"],
              supplier_hotel=review,
              match_score=review["rule_score"],
              mapping_type="MANUAL",
              is_manual_verified=True
          )

        # A reviewer-approved mapping was never registered against the master's
        # public id, so the row kept whatever anchor an earlier run gave it and
        # never merged the two ids. It also meant an approval could not
        # corroborate a provisional master.
        public_id = await self.identity_service.attach_to_master(
            review["suggested_master_hotel_id"],
            supplier_hotel_id,
        )

        # Recorded before the candidate row is deleted, and recorded at all
        # because an approval otherwise left no durable trace: the mapping lives
        # in hotel_mappings, a reset truncates it, and the next run re-derives
        # the same suggestion and queues the same review. A reviewer's afternoon
        # of approvals was undone by one reset with nothing to replay from.
        # Merges survive through anchors and rejections through non-merge
        # assertions; this was the one decision that did not.
        await self._record_attach_decision(
            supplier_hotel_id=supplier_hotel_id,
            chosen_public_id=public_id,
            chosen_master_hotel_id=review["suggested_master_hotel_id"],
            suggested_master_hotel_id=review["suggested_master_hotel_id"],
            agreed=True,
            reason="Reviewer approved the suggested match",
            actor="reviewer",
        )

        await self.mapping_service.update_queue_status(
          supplier_hotel_id,
          "Completed"
      )

        await self.mapping_service.delete_manual_review_candidate(
          supplier_hotel_id
      )

        await self.session.commit()
        
        return True

      except IntegrityError:

        await self.session.rollback()

        return False
      
    # How far out the picker looks before a reviewer has typed anything. Far
    # wider than the matcher's 1 km, because the whole reason a record reaches
    # this screen is that the matcher's reach was not enough.
    CANDIDATE_RADIUS_METERS = 25000

    async def master_candidates(
        self,
        supplier_hotel_id: int,
        query: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """
        Masters this record could be attached to, with the evidence for each.

        Every row carries distance and name agreement, because a reviewer
        picking from a bare list of names is only doing the matcher's job more
        slowly and with less information. The engine's own suggestion is
        included and marked, so agreeing with it is one click from the same
        screen as overruling it.
        """
        source = await self.get_supplier_hotel(supplier_hotel_id)

        if source is None:
            return {"source": None, "candidates": [], "count": 0}

        bound = {
            "row_id": supplier_hotel_id,
            "radius": self.CANDIDATE_RADIUS_METERS,
            "limit": limit,
            "like": f"%{query}%" if query else None,
        }

        # With a query the reviewer is looking somewhere specific, so the radius
        # must not silently exclude it; without one, proximity is the only
        # sensible starting point.
        scope = (
            "(m.hotel_name ILIKE :like OR m.city ILIKE :like)"
            if query
            else "ST_DWithin(m.geo_location, s.geo_location, :radius)"
        )

        result = await self.session.execute(
            text(
                f"""
                SELECT m.master_hotel_id,
                       r.public_id,
                       r.status,
                       m.hotel_name,
                       m.address,
                       m.city,
                       m.postal_code,
                       CASE WHEN s.geo_location IS NOT NULL
                                 AND m.geo_location IS NOT NULL
                            THEN round(ST_Distance(
                                     m.geo_location, s.geo_location)::numeric, 0)
                       END AS distance_meters,
                       round((similarity(lower(m.hotel_name),
                                         lower(s.hotel_name)) * 100)::numeric, 0)
                           AS name_match_pct,
                       (SELECT count(*) FROM hotel_mappings hm
                         WHERE hm.master_hotel_id = m.master_hotel_id)
                           AS provider_count,
                       (m.master_hotel_id = c.suggested_master_hotel_id)
                           AS is_suggested
                FROM supplier_hotels s
                JOIN manual_review_candidates c
                  ON c.supplier_hotel_id = s.id
                JOIN master_hotels m
                  ON {scope}
                LEFT JOIN master_hotel_registry r
                       ON r.master_hotel_id = m.master_hotel_id
                WHERE s.id = :row_id
                ORDER BY (m.master_hotel_id = c.suggested_master_hotel_id) DESC,
                         similarity(lower(m.hotel_name), lower(s.hotel_name)) DESC,
                         ST_Distance(m.geo_location, s.geo_location) ASC
                LIMIT :limit;
                """
            ),
            bound,
        )

        return {
            "source": {
                "supplier_name": source["supplier_name"],
                "supplier_hotel_id": source["supplier_hotel_id"],
                "hotel_name": source["hotel_name"],
                "address": source["address"],
                "city": source["city"],
                "latitude": source["latitude"],
                "longitude": source["longitude"],
            },
            "candidates": [dict(row) for row in result.mappings().all()],
            "searched": bool(query),
        }

    async def attach_to_existing_master(
        self,
        supplier_hotel_id: int,
        master_hotel_id: int,
        reason: str | None = None,
        actor: str = "reviewer",
    ) -> dict[str, Any]:
        """
        Attach a queued record to a master the reviewer chose, which need not be
        the one the engine suggested.

        Until now the queue offered three answers — accept the suggestion, make
        a new master, reject — and none of them is "you had the right idea and
        the wrong hotel". A reviewer who knew the correct master could only
        reject and hope the next run found it, which it would not: the run that
        produced this suggestion is the run that already failed to find the
        better one.

        Choosing a *different* master is also the most informative thing a
        reviewer does all day, so it is recorded as a label rather than being
        spent on one record and thrown away.
        """
        review = await self.get_review_record(supplier_hotel_id)

        if review is None:
            return {"ok": False, "error": "Record is not in the review queue"}

        if await self.mapping_exists(
            review["supplier_name"], review["supplier_hotel_id"]
        ):
            return {"ok": False, "error": "This record is already mapped"}

        supplier_hotel = await self.get_supplier_hotel(supplier_hotel_id)

        if supplier_hotel is None:
            return {"ok": False, "error": "Supplier record not found"}

        target = await self.session.execute(
            text(
                """
                SELECT m.master_hotel_id, m.hotel_name, r.public_id
                FROM master_hotels m
                LEFT JOIN master_hotel_registry r
                       ON r.master_hotel_id = m.master_hotel_id
                WHERE m.master_hotel_id = :master_hotel_id;
                """
            ),
            {"master_hotel_id": master_hotel_id},
        )

        chosen = target.mappings().first()

        if chosen is None:
            return {"ok": False, "error": "That master hotel does not exist"}

        suggested_master_hotel_id = review["suggested_master_hotel_id"]
        agreed = master_hotel_id == suggested_master_hotel_id

        try:
            await self.mapping_service.insert_hotel_mapping(
                master_hotel_id=master_hotel_id,
                supplier_hotel=supplier_hotel,
                # Only meaningful when the reviewer kept the suggestion. Against
                # any other master no score was ever computed, and carrying the
                # suggestion's score across would attach evidence to a pair it
                # was never measured on.
                match_score=review["rule_score"] if agreed else None,
                mapping_type="MANUAL",
                is_manual_verified=True,
            )

            # Binds the record to the master's public id, and promotes the
            # master out of Provisional if this is its second supplier.
            public_id = await self.identity_service.attach_to_master(
                master_hotel_id,
                supplier_hotel_id,
            )

            # Recorded with the id that survived the attach, not the one read
            # before it. Attaching can merge two public ids — this record's
            # anchor may already belong to an older id for the same property —
            # and a label naming the retired half would read as a disagreement
            # with itself.
            await self._record_attach_decision(
                supplier_hotel_id=supplier_hotel_id,
                chosen_public_id=public_id or chosen["public_id"],
                chosen_master_hotel_id=chosen["master_hotel_id"],
                suggested_master_hotel_id=suggested_master_hotel_id,
                agreed=agreed,
                reason=reason,
                actor=actor,
            )

            await self.mapping_service.update_queue_status(
                supplier_hotel_id, "Completed"
            )

            await self.mapping_service.delete_manual_review_candidate(
                supplier_hotel_id
            )

            await self.session.commit()

        except IntegrityError:
            await self.session.rollback()
            return {"ok": False, "error": "That mapping already exists"}

        return {
            "ok": True,
            "master_hotel_id": master_hotel_id,
            "public_id": public_id or chosen["public_id"],
            "master_hotel_name": chosen["hotel_name"],
            "agreed_with_suggestion": agreed,
        }

    async def _record_attach_decision(
        self,
        supplier_hotel_id: int,
        chosen_public_id: str,
        chosen_master_hotel_id: int,
        suggested_master_hotel_id: int | None,
        agreed: bool,
        reason: str | None,
        actor: str,
    ) -> None:
        """
        Persist the reviewer's choice as a labelled pair.

        Every attachment is a positive example, and a disagreement is
        simultaneously a negative one for the master the engine proposed — the
        two labels the thresholds most need and currently have none of. Keyed on
        public id so the label survives a pipeline reset; internal master ids do
        not.
        """
        if not chosen_public_id:
            # A master with no public id cannot be referenced durably. Losing
            # the label is bad; failing the reviewer's decision over it is
            # worse, so this warns rather than raising.
            logger.warning(
                "No public id for master %s — decision on supplier row %s "
                "applied but not recorded",
                chosen_master_hotel_id, supplier_hotel_id
            )
            return

        await self.session.execute(
            text(
                """
                INSERT INTO review_attach_decision (
                    supplier_hotel_row_id, chosen_public_id, suggested_public_id,
                    agreed_with_suggestion, chosen_master_hotel_id,
                    suggested_master_hotel_id, rule_score, ai_similarity,
                    distance_meters, review_type, reason, decided_by
                )
                SELECT
                    :supplier_hotel_id,
                    :chosen_public_id,
                    (SELECT public_id FROM master_hotel_registry
                      WHERE master_hotel_id = :suggested_master_hotel_id
                      LIMIT 1),
                    :agreed,
                    :chosen_master_hotel_id,
                    :suggested_master_hotel_id,
                    c.rule_score, c.ai_similarity, c.distance_meters,
                    c.review_type,
                    :reason,
                    :actor
                FROM manual_review_candidates c
                WHERE c.supplier_hotel_id = :supplier_hotel_id;
                """
            ),
            {
                "supplier_hotel_id": supplier_hotel_id,
                "chosen_public_id": chosen_public_id,
                "chosen_master_hotel_id": chosen_master_hotel_id,
                "suggested_master_hotel_id": suggested_master_hotel_id,
                "agreed": agreed,
                "reason": reason,
                "actor": actor,
            },
        )

    async def create_new_master(
    self,
    supplier_hotel_id: int
) -> bool:

        review = await self.get_review_record(
            supplier_hotel_id
        )

        if review is None:
            return False

        supplier_hotel = await self.get_supplier_hotel(
            supplier_hotel_id
        )

        if supplier_hotel is None:
            return False

        try:

            new_master_hotel_id = await self.mapping_service.create_master_hotel_from_supplier(
                supplier_hotel
            )
            # Registered Active, not Provisional: a reviewer looking at the
            # record and its suggested master has said this is a distinct
            # property, and that decision is the corroboration. Registration was
            # missing here entirely, so masters created from review had no
            # public id at all and never appeared in the console or exports.
            await self.identity_service.register_master(
                new_master_hotel_id,
                supplier_hotel_id,
                status="Active",
            )
            # Generate embedding for the newly created master
            await self.master_embedding_service.generate_embedding_for_master(
                new_master_hotel_id
            )
            # MANUAL_NEW_MASTER, not NEW_MASTER: the pipeline sets
            # is_manual_verified=True on its own new masters too, so the
            # mapping_type is the only thing that separates a reviewer's
            # decision from an automatic one in the per-supplier breakdown.
            await self.mapping_service.insert_hotel_mapping(
                master_hotel_id=new_master_hotel_id,
                supplier_hotel=supplier_hotel,
                match_score=100.0,
                mapping_type="MANUAL_NEW_MASTER",
                is_manual_verified=True
            )

            await self.mapping_service.update_queue_status(
                supplier_hotel_id,
                "Completed"
            )

            await self.mapping_service.delete_manual_review_candidate(
                supplier_hotel_id
            )

            await self.session.commit()

            return True

        except Exception as error:

            await self.session.rollback()

            print(
                f"Error creating new master hotel "
                f"for supplier hotel {supplier_hotel_id}: {error}"
            )

            return False
          
          
    async def replay_reviewer_decisions(self) -> dict[str, Any]:
        """
        Re-apply every recorded reviewer decision after a rebuild.

        Recording a decision is not the same as keeping it. A reset truncates
        `hotel_mappings` and returns the queue to Pending; the next run scores
        the same record, reaches the same suggestion and queues the same review.
        Without this, an afternoon of approvals is undone by one reset and the
        reviewer has no way to tell which of their decisions survived.

        Runs after the queue drains, so the pipeline has already placed every
        record and this only has to correct it. Where the two disagree the
        reviewer wins — that is the whole point of the queue, and it is why this
        moves a mapping rather than only filling gaps.

        Decisions are keyed on public id and resolved forward through merges, so
        a decision made before two ids were merged still lands on the surviving
        master rather than a retired one.
        """
        decisions = await self.session.execute(
            text(
                """
                SELECT DISTINCT ON (supplier_hotel_row_id)
                       supplier_hotel_row_id, chosen_public_id, decided_by
                FROM review_attach_decision
                ORDER BY supplier_hotel_row_id, created_at DESC;
                """
            )
        )

        applied = skipped = already = 0

        for decision in decisions.mappings().all():
            row_id = decision["supplier_hotel_row_id"]

            surviving = await self.identity_service.resolve(
                decision["chosen_public_id"]
            )

            if surviving is None:
                skipped += 1
                continue

            target = await self.session.execute(
                text(
                    """
                    SELECT master_hotel_id FROM master_hotel_registry
                    WHERE public_id = :public_id
                      AND master_hotel_id IS NOT NULL;
                    """
                ),
                {"public_id": surviving},
            )
            master_hotel_id = target.scalar()

            if master_hotel_id is None:
                # The id survived but nothing carries it this run — the master
                # it described was never rebuilt. Nothing to attach to.
                skipped += 1
                continue

            current = await self.session.execute(
                text(
                    """
                    SELECT master_hotel_id FROM hotel_mappings
                    WHERE supplier_hotel_row_id = :row_id;
                    """
                ),
                {"row_id": row_id},
            )
            current_master = current.scalar()

            if current_master == master_hotel_id:
                already += 1
                continue

            supplier_hotel = await self.get_supplier_hotel(row_id)

            if supplier_hotel is None:
                skipped += 1
                continue

            try:
                if current_master is not None:
                    await self.session.execute(
                        text(
                            """
                            DELETE FROM hotel_mappings
                            WHERE supplier_hotel_row_id = :row_id;
                            """
                        ),
                        {"row_id": row_id},
                    )

                await self.mapping_service.insert_hotel_mapping(
                    master_hotel_id=master_hotel_id,
                    supplier_hotel=supplier_hotel,
                    match_score=None,
                    mapping_type="MANUAL",
                    is_manual_verified=True,
                )

                await self.identity_service.attach_to_master(
                    master_hotel_id, row_id
                )

                await self.mapping_service.delete_manual_review_candidate(row_id)
                await self.mapping_service.update_queue_status(row_id, "Completed")

                await self.session.commit()
                applied += 1

            except Exception:
                await self.session.rollback()
                logger.exception(
                    "Could not replay reviewer decision for supplier row %s", row_id
                )
                skipped += 1

        if applied or skipped:
            logger.info(
                "Reviewer decisions replayed: %d re-applied, %d already correct, "
                "%d skipped",
                applied, already, skipped
            )

        return {
            "reapplied": applied,
            "already_correct": already,
            "skipped": skipped,
        }

    async def decide_batch(
        self,
        supplier_hotel_ids: list[int],
        action: str,
        reason: str | None = None,
        actor: str = "reviewer",
    ) -> dict[str, Any]:
        """
        Apply one decision to several queued records.

        583 of the 1,295 queued items ask the same question — an exact name at a
        distance geography cannot explain — and a reviewer who has satisfied
        themselves about the pattern should not have to click through it 583
        times. The audit called the old queue "99% noise"; that was a throughput
        problem, not a fault in any individual suggestion.

        This decides nothing on the reviewer's behalf. The caller sends an
        explicit list of ids it has displayed, so every record in a batch has
        been on screen with its evidence beside it. What is removed is the
        clicking, not the looking — which is why there is no "approve
        everything" endpoint taking a filter instead of ids.

        Records are applied one at a time and reported by id. A batch that fails
        halfway must not leave the reviewer guessing which half.
        """
        if action not in ("approve", "reject"):
            return {"ok": False, "error": f"Unknown action '{action}'"}

        if not supplier_hotel_ids:
            return {"ok": False, "error": "No records selected"}

        if len(supplier_hotel_ids) > BATCH_DECISION_LIMIT:
            return {
                "ok": False,
                "error": (
                    f"Batch of {len(supplier_hotel_ids)} exceeds the "
                    f"{BATCH_DECISION_LIMIT} record limit"
                ),
            }

        succeeded, failed = [], []

        for supplier_hotel_id in supplier_hotel_ids:
            try:
                if action == "approve":
                    done = await self.approve_match(supplier_hotel_id)
                else:
                    done = await self.reject_review(
                        supplier_hotel_id,
                        reason or "Reviewer rejected the suggested match",
                        actor,
                    )
            except Exception:
                # One bad record must not abandon the rest of the reviewer's
                # work; it is reported instead.
                logger.exception(
                    "Batch %s failed for supplier hotel %s",
                    action, supplier_hotel_id
                )
                await self.session.rollback()
                done = False

            (succeeded if done else failed).append(supplier_hotel_id)

        logger.info(
            "Batch %s by %s: %d applied, %d failed",
            action, actor, len(succeeded), len(failed)
        )

        return {
            "ok": True,
            "action": action,
            "applied": len(succeeded),
            "failed": len(failed),
            "failed_ids": failed,
        }

    async def reject_review(
    self,
    supplier_hotel_id: int,
    reason: str = "Reviewer rejected the suggested match",
    actor: str = "reviewer",
) -> bool:
        """
        The suggested master is wrong. Record that as a durable decision, then
        return the record to the queue to find a different master or become its
        own.

        The assertion is what makes reject terminate. Without it the record goes
        back to Pending, the next run scores the same candidates, reaches the
        same suggestion and queues the same review — the reviewer's decision
        lasts exactly until the next run. That was survivable while review was
        rare; it is not now that every exact-name conflict lands here.

        Both candidate queries already exclude pairs under an assertion, so one
        row is enough to stop the loop.
        """
        review = await self.get_review_record(
            supplier_hotel_id
        )

        if review is None:
            return False

        try:

            await self.session.execute(
                text(
                    """
                    INSERT INTO master_non_merge_assertion
                        (row_id_a, row_id_b, reason, asserted_by)
                    SELECT LEAST(:row_id, hm.supplier_hotel_row_id),
                           GREATEST(:row_id, hm.supplier_hotel_row_id),
                           :reason,
                           :actor
                    FROM hotel_mappings hm
                    WHERE hm.master_hotel_id = :master_hotel_id
                      AND hm.supplier_hotel_row_id IS NOT NULL
                      AND hm.supplier_hotel_row_id <> :row_id
                    ON CONFLICT (row_id_a, row_id_b) DO NOTHING;
                    """
                ),
                {
                    "row_id": supplier_hotel_id,
                    "master_hotel_id": review["suggested_master_hotel_id"],
                    "reason": reason,
                    "actor": actor,
                }
            )

            await self.mapping_service.delete_manual_review_candidate(
                supplier_hotel_id
            )

            await self.mapping_service.update_queue_status(
                supplier_hotel_id,
                "Pending"
            )

            await self.session.commit()

            return True

        except Exception:

            await self.session.rollback()

            return False