import logging
import re
from collections import Counter
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.matching_service import MatchingService
from app.matching.ai_integration_service import AIIntegrationService
from app.services.hotel_mapping_service import HotelMappingService
from app.matching.master_embedding_service import MasterEmbeddingService
from app.services.master_identity_service import MasterIdentityService
from config import settings

logger = logging.getLogger(__name__)

# How far the pre-creation net looks for a master that already means this hotel.
#
# Matched to the exact-name escalation (5 km) on purpose: the two checks address
# the same failure — supplier coordinates disagreeing by more than the matcher's
# 1 km reach — and differ only in how the names agree. Beyond this, "same name,
# same city" stops being coordinate error and starts being two real branches of
# one chain; 56 such master pairs sit more than 5 km apart in the current data.
SEMANTIC_DUPLICATE_RADIUS_METERS = 5000.0

# How long a claimed row may sit in 'Processing' before it is assumed abandoned.
# Comfortably longer than any real batch — a claim of 500 hotels finishes in
# about a minute — so this only ever fires on a worker that actually died.
STALE_CLAIM_MINUTES = 15


class QueueProcessingService:
    """
    Async queue processing service.

    This is the final entry point for queue processing.
    AIIntegrationService should be plugged in after MatchingService returns
    the rule-based result and before final decision is applied.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
         
        self.matching_service = MatchingService(session)
        self.ai_service = AIIntegrationService(session)
        self.mapping_service = HotelMappingService(session)
        self.master_embedding_service = MasterEmbeddingService(session)
        self.identity_service = MasterIdentityService(session)

    async def claim_pending_batch(self, limit: int = 500):
        """
        Atomically claim a batch of pending queue rows.

        FOR UPDATE SKIP LOCKED lets several workers draw from the same queue
        without ever handing the same hotel to two of them. The previous plain
        SELECT allowed overlapping reads, which produced duplicate mappings and
        duplicate masters as soon as concurrency was above one.

        Claiming and marking Processing happen in a single statement, so a hotel
        cannot be claimed twice even if a worker dies immediately afterwards.

        A claim also expires. 'Processing' used to be a terminal state by
        accident: a worker that died mid-batch left its rows there permanently,
        and because the drain loop only ever looked for 'Pending' the next run
        announced "no pending hotels remaining" and reported success while those
        records sat unprocessed and uncounted. A claim older than
        STALE_CLAIM_MINUTES is therefore treated as abandoned and re-offered,
        which is safe under concurrency because SKIP LOCKED still applies and
        the timeout is far longer than any real batch.
        """
        result = await self.session.execute(
            text(
                """
                UPDATE hotel_mapping_queue
                SET status = 'Processing',
                    claimed_at = NOW()
                WHERE id IN (
                    SELECT id
                    FROM hotel_mapping_queue
                    WHERE status = 'Pending'
                       OR (status = 'Processing'
                           AND claimed_at < NOW()
                               - make_interval(mins => :stale_minutes))
                    ORDER BY id
                    FOR UPDATE SKIP LOCKED
                    LIMIT :limit
                )
                RETURNING supplier_hotel_id;
                """
            ),
            {"limit": limit, "stale_minutes": STALE_CLAIM_MINUTES}
        )

        claimed = [row[0] for row in result.fetchall()]

        await self.session.commit()

        return claimed

    async def refresh_derived_features(self) -> int:
        """
        Rebuild corpus-derived features before scoring anything.

        Called here rather than only at import because there is more than one
        way records reach this queue — the CSV importer, the file/database
        import screens, a manual insert — and a feature that is only correct on
        some of those paths is worse than one that is absent, because nothing
        announces which path was taken.

        Word frequencies are a property of the whole corpus, so adding a
        supplier changes what counts as a distinctive name for every record
        already present. Recomputing is a single pass and costs far less than
        scoring a run against a previous feed's vocabulary.
        """
        result = await self.session.execute(
            text("SELECT refresh_name_token_df();")
        )

        tokens = result.scalar() or 0

        await self.session.commit()

        if tokens == 0:
            # Never silently. A feature scoring 0 for every pair is exactly how
            # star and chain sat dead in the weights for months — visible only
            # as a matcher that was mysteriously worse than its design.
            logger.error(
                "name_token_df is empty — shared-token rarity will score 0 for "
                "every pair. Check that supplier_hotels has rows."
            )
        else:
            logger.info("Derived features refreshed: %d name tokens indexed", tokens)

        return tokens

    @staticmethod
    def review_type_for(score: dict) -> str:
        """
        Why this record is in front of a person. Drives triage: an exact-name
        geo conflict is a near-certain duplicate awaiting one click, while a
        borderline composite score genuinely needs the reviewer to think.
        """
        # A stage that already knows why it escalated says so directly, rather
        # than having its reason inferred from fields it never set.
        if score.get("review_type"):
            return score["review_type"]

        exact_name_class = score.get("exact_name_class")

        if exact_name_class == "STRICT":
            return "EXACT_NAME_GEO_CONFLICT"

        if exact_name_class == "CORE_ONLY":
            return "CITY_STRIPPED_NAME_MATCH"

        return "SCORE_BAND"

    async def insert_manual_review_candidate(
        self,
        supplier_hotel_record_id: int,
        best_candidate: dict
) -> None:
        score = best_candidate["score"]

        await self.session.execute(
            text(
                """
                INSERT INTO manual_review_candidates (
                    supplier_hotel_id,
                    suggested_master_hotel_id,
                    rule_score,
                    ai_similarity,
                    decision_reason,
                    review_type,
                    distance_meters
                )
                VALUES (
                    :supplier_hotel_id,
                    :suggested_master_hotel_id,
                    :rule_score,
                    :ai_similarity,
                    :decision_reason,
                    :review_type,
                    :distance_meters
                )
                -- Re-processing a record that was rejected back to Pending used
                -- to insert a second identical review item every run.
                ON CONFLICT (supplier_hotel_id) DO UPDATE SET
                    suggested_master_hotel_id = EXCLUDED.suggested_master_hotel_id,
                    rule_score      = EXCLUDED.rule_score,
                    ai_similarity   = EXCLUDED.ai_similarity,
                    decision_reason = EXCLUDED.decision_reason,
                    review_type     = EXCLUDED.review_type,
                    distance_meters = EXCLUDED.distance_meters;
                """
            ),
            {
                "supplier_hotel_id": supplier_hotel_record_id,
                "suggested_master_hotel_id": best_candidate["master_hotel_id"],
                "rule_score": score["rule_score"],
                "ai_similarity": score.get("ai_similarity"),
                "decision_reason": score.get("decision_reason"),
                "review_type": self.review_type_for(score),
                "distance_meters": score.get("distance_meters"),
            }
        )
    
    async def _address_duplicate_master(self, supplier_hotel: dict):
        """
        An existing master with the same postcode AND building number as this
        record, guarded by name agreement so two different businesses at one
        plot do not pair. Distance-independent by design — this is the coordinate
        it is meant to survive.

        Returns the nearest such master (nearest only to give the reviewer the
        most plausible one first), or None.
        """
        postal = (supplier_hotel.get("postal_code") or "").strip()
        address = supplier_hotel.get("address") or ""
        strict_name = (supplier_hotel.get("strict_name") or "").strip()

        match = re.search(r"\d+", address)
        building = match.group(0) if match else None

        lat = supplier_hotel.get("latitude")
        lon = supplier_hotel.get("longitude")

        if lat is None or lon is None:
            # No coordinate to rank by; the completeness gate normally prevents
            # this reaching CREATE_NEW_MASTER, but guard rather than assume.
            return None

        # A WKT literal built here, not a bound parameter, so asyncpg never has
        # to infer a geography type it cannot — passing lat/lon as bare params
        # inside ST_MakePoint raised AmbiguousParameterError.
        point = f"SRID=4326;POINT({float(lon)} {float(lat)})"

        # The match arms are assembled here rather than left in SQL with NULL
        # guards, because a bound parameter that appears only inside `IS NOT NULL`
        # gives asyncpg no type to infer and raises AmbiguousParameterError. Each
        # arm is added only when its inputs exist, so no untyped NULL is ever
        # bound.
        params = {
            "core_name": supplier_hotel.get("core_name") or "",
            "hotel_name": supplier_hotel.get("hotel_name") or "",
        }
        arms = []

        # Identical full name (city included) is the strongest coordinate- and
        # postcode-independent signal there is: "Pride Plaza Hotel Ahmedabad"
        # exists twice, 5.8 km apart with postcodes 380054 and 380053, and no
        # postcode- or distance-bound net could see it. Two properties do not
        # share a full name INCLUDING the city, so this is one hotel with a bad
        # coordinate. Requires two or more tokens so a generic one-word strict
        # name ("grand", "palace") does not pair unrelated hotels across cities.
        if strict_name and len(strict_name.split()) >= 2:
            params["strict_name"] = strict_name
            arms.append("lower(m.strict_name) = lower(:strict_name)")

        # Same postcode + building number + name agreement — "JP Chennai" /
        # "Jp Hotel", both 1131 / 600107, different names.
        if postal and building is not None:
            params["postal"] = postal
            params["building"] = building
            arms.append(
                "(m.postal_code = :postal "
                " AND (regexp_match(coalesce(m.address, ''), '([0-9]+)'))[1] = :building "
                " AND (lower(m.core_name) = lower(:core_name) "
                "      OR similarity(lower(m.hotel_name), lower(:hotel_name)) > 0.3))"
            )

        if not arms:
            return None

        result = await self.session.execute(
            text(
                f"""
                SELECT m.master_hotel_id, m.postal_code,
                       (regexp_match(coalesce(m.address, ''), '([0-9]+)'))[1]
                           AS building_num,
                       round(ST_Distance(
                           m.geo_location, '{point}'::geography
                       )::numeric, 0) AS distance_meters
                FROM master_hotels m
                JOIN master_hotel_registry r
                  ON r.master_hotel_id = m.master_hotel_id
                 AND r.status IN ('Active', 'Provisional')
                WHERE ({' OR '.join(arms)})
                ORDER BY ST_Distance(m.geo_location, '{point}'::geography)
                         ASC NULLS LAST
                LIMIT 1;
                """
            ),
            params,
        )

        row = result.mappings().first()

        return dict(row) if row else None

    async def check_semantic_duplicate_before_creating(
        self,
        match_result: dict[str, Any]
    ) -> None:
        """
        Last check before a record becomes its own master: does a master that
        means the same hotel already exist nearby?

        Escalates to MANUAL_REVIEW and never further. The model is good at
        "these two strings describe the same kind of thing" and bad at the
        distinction that actually matters here — "Zone By The Park Coimbatore"
        and "Zone Connect by The Park Coimbatore" are different hotels 6.8 km
        apart and would score high — so it is allowed to raise the question and
        never to answer it. A wrong escalation costs a reviewer a few seconds; a
        wrong merge is unrecoverable in a booking path.
        """
        decision = match_result.get(
            "final_decision",
            match_result.get("rule_decision")
        )

        if decision != "CREATE_NEW_MASTER":
            return

        supplier_hotel = match_result.get("supplier_hotel")

        if not supplier_hotel:
            return

        supplier_hotel_record_id = match_result["supplier_hotel_record_id"]

        # First, the check a wrong coordinate cannot escape: an existing master
        # with the same postcode AND building number. The semantic net below
        # only reaches 5 km, so a supplier coordinate kilometres off — Sabre's
        # "Jp Hotel" 11 km from "JP Chennai", same 1131 / 600107 — slips past it
        # and becomes a duplicate master on every run. Postcode and building
        # number do not move when the GPS is wrong.
        address_dup = await self._address_duplicate_master(supplier_hotel)

        if address_dup is not None:
            match_result["final_decision"] = "MANUAL_REVIEW"
            match_result["best_candidate"] = {
                "master_hotel_id": address_dup["master_hotel_id"],
                "score": {
                    "rule_score": None,
                    "ai_similarity": None,
                    "distance_meters": address_dup["distance_meters"],
                    "review_type": "ADDRESS_DUPLICATE",
                    "decision_reason": (
                        "About to create a new master, but an existing master "
                        "has the same postcode and building number "
                        f"({address_dup['postal_code']} / "
                        f"{address_dup['building_num']}) — likely one hotel with "
                        "a bad coordinate; human decision required"
                    ),
                },
            }
            logger.info(
                "Hotel %s escalated: same postcode+building as master %s",
                supplier_hotel_record_id, address_dup["master_hotel_id"]
            )
            return

        try:
            duplicate = await self.ai_service.semantic_duplicate_for(
                supplier_hotel_row_id=supplier_hotel_record_id,
                hotel_name=supplier_hotel.get("hotel_name") or "",
                address=supplier_hotel.get("address") or "",
                city=supplier_hotel.get("city") or "",
                country=supplier_hotel.get("country") or "",
                radius_meters=SEMANTIC_DUPLICATE_RADIUS_METERS,
            )
        except Exception:
            # The net is an improvement on the outcome, not a precondition for
            # it. If embedding or pgvector is unavailable the record still
            # becomes a master, exactly as it did before this check existed.
            logger.exception(
                "Semantic duplicate check failed for hotel %s — "
                "proceeding with CREATE_NEW_MASTER",
                supplier_hotel_record_id
            )
            return

        if duplicate is None:
            return

        match_result["final_decision"] = "MANUAL_REVIEW"

        match_result["best_candidate"] = {
            "master_hotel_id": duplicate["master_hotel_id"],
            "score": {
                # No rule score exists: the rule engine never compared these two
                # records. Recording 0 would read as "scored and rejected".
                "rule_score": None,
                "ai_similarity": round(duplicate["ai_similarity"] * 100, 2),
                "distance_meters": None,
                "review_type": "AI_SEMANTIC_DUPLICATE",
                "decision_reason": (
                    f"About to create a new master, but an existing master "
                    f"within {int(SEMANTIC_DUPLICATE_RADIUS_METERS)} m means the "
                    f"same hotel (AI similarity "
                    f"{duplicate['ai_similarity']:.2f}) — human decision required"
                ),
            },
        }

        logger.info(
            "Hotel %s escalated to review: semantic duplicate of master %s (%.2f)",
            supplier_hotel_record_id,
            duplicate["master_hotel_id"],
            duplicate["ai_similarity"],
        )

    async def apply_rule_decision(
    self,
    match_result: dict[str, Any]
) -> dict[str, Any]:
        supplier_hotel = match_result["supplier_hotel"]
        supplier_hotel_record_id = match_result["supplier_hotel_record_id"]
        decision = match_result.get(
                  "final_decision",
                  match_result["rule_decision"]
              )
        best_candidate = match_result["best_candidate"]

        if decision == "SUPPLIER_NOT_FOUND":
            return {
                "supplier_hotel_record_id": supplier_hotel_record_id,
                "status": "FAILED",
                "message": "Supplier hotel not found"
            }

        if decision == "FLAGGED":
            reason = match_result.get("flag_reason", "UNSPECIFIED")

            await self.session.execute(
                text(
                    """
                    INSERT INTO flagged_records (
                        supplier_hotel_id, flag_reason, status
                    )
                    VALUES (:supplier_hotel_id, :flag_reason, 'Flagged')
                    ON CONFLICT (supplier_hotel_id) DO UPDATE
                       SET flag_reason = EXCLUDED.flag_reason;
                    """
                ),
                {
                    "supplier_hotel_id": supplier_hotel_record_id,
                    "flag_reason": reason,
                }
            )

            await self.mapping_service.update_queue_status(
                supplier_hotel_record_id,
                "Flagged"
            )

            return {
                "supplier_hotel_record_id": supplier_hotel_record_id,
                "status": "Flagged",
                "decision": "FLAGGED",
                "flag_reason": reason
            }

        if decision == "AUTO_MATCH":
            rule_score = best_candidate["score"]["rule_score"]

            await self.mapping_service.insert_hotel_mapping(
                master_hotel_id=best_candidate["master_hotel_id"],
                supplier_hotel=supplier_hotel,
                match_score=rule_score,
                mapping_type="AUTO",
                is_manual_verified=False,
                evidence=best_candidate["score"],
        )

            # Record that this supplier row now belongs to the master's stable
            # public id. If it previously belonged to another, the two ids
            # describe one property and are merged rather than diverging.
            public_id = await self.identity_service.attach_to_master(
                best_candidate["master_hotel_id"],
                supplier_hotel_record_id,
            )

            await self.mapping_service.update_queue_status(
                supplier_hotel_record_id,
                "Completed"
            )

            return {
                "supplier_hotel_record_id": supplier_hotel_record_id,
                "status": "Completed",
                "decision": "AUTO_MATCH",
                "public_id": public_id,
                "best_candidate": best_candidate
            }

        if decision == "MANUAL_REVIEW":

            await self.insert_manual_review_candidate(
                supplier_hotel_record_id,
                best_candidate
            )

            await self.mapping_service.update_queue_status(
                supplier_hotel_record_id,
                "ManualReview"
            )

            return {
                "supplier_hotel_record_id": supplier_hotel_record_id,
                "status": "ManualReview",
                "decision": "MANUAL_REVIEW",
                "best_candidate": best_candidate
            }

        if decision == "CREATE_NEW_MASTER":
            new_master_hotel_id = await self.mapping_service.create_master_hotel_from_supplier(
    supplier_hotel
)
            # Reclaims the public id this supplier row held in an earlier run,
            # so a full rebuild reproduces the same ids for the same data.
            #
            # Registered Provisional: one supplier saying a hotel exists is not
            # yet a master anyone should be able to book against. It becomes
            # Active the moment a second supplier attaches, or when a reviewer
            # confirms it from the provisional queue.
            public_id = await self.identity_service.register_master(
                new_master_hotel_id,
                supplier_hotel_record_id,
            )
            await self.master_embedding_service.generate_embedding_for_master(
    new_master_hotel_id
)
            await self.mapping_service.insert_hotel_mapping(
                master_hotel_id=new_master_hotel_id,
                supplier_hotel=supplier_hotel,
                match_score=100.0,
                mapping_type="NEW_MASTER",
                is_manual_verified=True
        )

            await self.mapping_service.update_queue_status(
                supplier_hotel_record_id,
                "Completed"
            )

            return {
                "supplier_hotel_record_id": supplier_hotel_record_id,
                "status": "Completed",
                "decision": "CREATE_NEW_MASTER",
                "public_id": public_id,
                "new_master_hotel_id": new_master_hotel_id,
                "master_status": "Provisional",
            }

        return {
            "supplier_hotel_record_id": supplier_hotel_record_id,
            "status": "FAILED",
            "message": f"Unknown decision: {decision}"
        }

    async def process_one_supplier_hotel(
        self,
        supplier_hotel_record_id: int,
        apply_decision: bool = True
    ):
        # The row was already claimed and marked Processing by
        # claim_pending_batch(), so there is no separate status write here.
        match_result = await self.matching_service.score_supplier_hotel(
            supplier_hotel_record_id
        )

        logger.debug(
            "Hotel %s rule_decision=%s",
            supplier_hotel_record_id,
            match_result.get("rule_decision")
        )

        best_candidate = match_result.get("best_candidate")

        if best_candidate:

            score = best_candidate["score"]

            # AI validation now runs on every borderline candidate that cleared
            # the tier gate, keyed off the decision rather than a hardcoded score
            # window that no longer matches the scoring scale.
            if match_result.get("rule_decision") == "MANUAL_REVIEW":

                rule_candidates = match_result.get("candidates", [])

                enriched_candidate = await self.ai_service.enrich_candidate(
                    best_candidate,
                    rule_candidates
                )

                match_result["best_candidate"] = enriched_candidate

                match_result["final_decision"] = (
                    enriched_candidate["score"]["final_decision"]
                )

        # Pre-creation safety net.
        #
        # Runs last, on whatever decision survived everything above, because the
        # duplicates that matter reach CREATE_NEW_MASTER by several different
        # routes: no candidate inside 1 km, a candidate that failed the tier
        # gate, or the AI cancelling a borderline suggestion. Two records for
        # one hotel whose coordinates disagree by more than a kilometre — Zone
        # by The Park at 3177 m, Mementos at 1608 m — were never compared at
        # all, so no threshold could have saved them. This is the last point at
        # which that is still recoverable.
        await self.check_semantic_duplicate_before_creating(match_result)

        if not apply_decision:
            # Dry run. The row must reach a terminal state — returning it to
            # Pending here made the drain loop re-claim it forever.
            await self.mapping_service.update_queue_status(
                supplier_hotel_record_id,
                "Skipped"
            )

            return {
                "match_result": match_result,
                "final_result": {
                    "supplier_hotel_record_id": supplier_hotel_record_id,
                    "status": "Skipped",
                    "decision": match_result.get("rule_decision"),
                },
            }

        final_result = await self.apply_rule_decision(match_result)

        return {
            "match_result": match_result,
            "final_result": final_result
        }

    async def process_pending_batch(
        self,
        limit: int = None,
        apply_decision: bool = True
    ):
        """
        Drain the pending queue in claimed batches.

        Returns counters only. The previous version appended every result — the
        full supplier row plus up to 10 scored candidates, ~16 KB per hotel — to
        a list that lived until the whole queue drained. At 10 lakh records that
        is roughly 15 GB of retained Python objects and the worker is killed long
        before it finishes. Per-hotel detail belongs in the database, which
        already has it.
        """
        batch_size = limit or settings.QUEUE_CLAIM_BATCH

        await self.refresh_derived_features()

        logger.info("Starting queue processing (batch size %d)", batch_size)

        counters = Counter()
        total_processed = 0
        batch_number = 0

        while True:

            claimed_ids = await self.claim_pending_batch(batch_size)

            if not claimed_ids:
                logger.info("No pending hotels remaining.")
                break

            batch_number += 1

            for supplier_hotel_record_id in claimed_ids:
                try:
                    result = await self.process_one_supplier_hotel(
                        supplier_hotel_record_id,
                        apply_decision=apply_decision
                    )

                    await self.session.commit()

                    outcome = (
                        result.get("final_result", {}).get("decision")
                        if isinstance(result, dict) else None
                    )
                    counters[outcome or "PROCESSED"] += 1

                except Exception as error:
                    logger.warning(
                        "Error processing hotel %s: %s",
                        supplier_hotel_record_id,
                        error
                    )

                    await self.session.rollback()

                    try:
                        await self.mapping_service.update_queue_status(
                            supplier_hotel_record_id,
                            "Failed"
                        )
                        await self.session.commit()

                    except Exception as status_error:
                        logger.error(
                            "Error updating queue status for %s: %s",
                            supplier_hotel_record_id,
                            status_error
                        )
                        await self.session.rollback()

                    counters["FAILED"] += 1

                total_processed += 1

            logger.info(
                "Batch %d complete (%d hotels). Total processed: %d",
                batch_number,
                len(claimed_ids),
                total_processed
            )

        # After the queue drains, not during it. The pipeline has now placed
        # every record, so this only has to correct the ones a reviewer already
        # ruled on — and where the two disagree the reviewer wins, which is the
        # whole point of having a queue.
        replayed = await self.replay_reviewer_decisions()

        gate = await self.check_accuracy_gate()

        return {
            "processed_count": total_processed,
            "batches": batch_number,
            "outcomes": dict(counters),
            "reviewer_decisions": replayed,
            "accuracy_gate": gate,
            "message": "Queue processing completed",
        }

    async def check_accuracy_gate(self) -> dict:
        """
        The release gate, checked at the end of every run.

        The point is that it is loud. A false merge in the published set is the
        one failure the whole design exists to prevent, and a change that
        introduced one used to be invisible until someone happened to look —
        this logs it at ERROR the moment a run produces it, so a regression
        announces itself in the same place the run does.
        """
        from app.services.accuracy_evaluation_service import AccuracyEvaluationService

        try:
            gate = await AccuracyEvaluationService(self.session).gate_status()
        except Exception:
            logger.exception("Accuracy gate check failed")
            return {"passing": None, "measured": False}

        if not gate["measured"]:
            logger.info("Accuracy gate: not measured (%s)", gate["reason"])
        elif gate["passing"]:
            logger.info(
                "Accuracy gate PASSED: 0 wrong merges across %d published masters",
                gate["masters_published"],
            )
        else:
            logger.error(
                "ACCURACY GATE FAILED: %d wrong merge(s) in the published set — "
                "a regression reached what consumers see",
                gate["published_false_merges"],
            )

        return gate

    async def replay_reviewer_decisions(self) -> dict:
        """
        Re-apply everything a reviewer decided, in the order the decisions
        depend on each other.

        Merges first: an attachment names the master it was made against by
        public id, and if two ids were merged after that decision the surviving
        one has to exist before the attachment resolves forward onto it.
        """
        from app.services.manual_review_service import ManualReviewService
        from app.services.master_lifecycle_service import MasterLifecycleService

        merges = {"reapplied": 0, "already_merged": 0, "skipped": 0}
        attaches = {"reapplied": 0, "already_correct": 0, "skipped": 0}

        try:
            merges = await MasterLifecycleService(self.session).replay_merge_decisions()
        except Exception:
            # A run that finished must not be reported as failed because the
            # replay stumbled; the decisions are still on record either way.
            logger.exception("Replaying merge decisions failed")

        try:
            attaches = await ManualReviewService(self.session).replay_reviewer_decisions()
        except Exception:
            logger.exception("Replaying attach decisions failed")

        return {"merges": merges, "attachments": attaches}