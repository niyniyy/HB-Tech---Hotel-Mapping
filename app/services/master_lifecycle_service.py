import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.master_identity_service import MasterIdentityService

logger = logging.getLogger(__name__)


class MasterLifecycleService:
    """
    Operator actions on master hotels: split, deprecate, reactivate.

    Split is not the inverse of merge. Merging is lossless — anyone holding
    either id still gets the right property. Splitting is not: an id that
    conflated two hotels was cached by consumers who cannot now be told which
    half they meant, because nothing recorded it. So a split records a permanent
    assertion that the two must never be merged again, and emits a change event
    for consumers to re-resolve.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.identity = MasterIdentityService(session)

    # ── read ────────────────────────────────────────────────────────────────

    async def get_master_detail(self, public_id: str):
        """Everything a reviewer needs to judge one master."""
        resolved = await self.identity.resolve(public_id)

        if resolved is None:
            return None

        header = await self.session.execute(
            text(
                """
                SELECT r.public_id, r.status, r.superseded_by, r.master_hotel_id,
                       r.deprecation_reason, r.first_seen, r.last_seen,
                       m.hotel_name, m.address, m.city, m.state, m.country,
                       m.postal_code, m.latitude, m.longitude, m.star_rating
                FROM master_hotel_registry r
                LEFT JOIN master_hotels m ON m.master_hotel_id = r.master_hotel_id
                WHERE r.public_id = :public_id;
                """
            ),
            {"public_id": resolved}
        )

        row = header.mappings().first()

        if row is None:
            return None

        members = await self.session.execute(
            text(
                """
                SELECT s.id AS supplier_hotel_row_id,
                       s.supplier_name, s.supplier_hotel_id,
                       s.hotel_name, s.address, s.city, s.postal_code,
                       s.latitude, s.longitude,
                       hm.match_score, hm.mapping_type, hm.confidence_tier,
                       hm.name_similarity, hm.distance_meters,
                       hm.is_manual_verified
                FROM hotel_mappings hm
                JOIN supplier_hotels s ON s.id = hm.supplier_hotel_row_id
                WHERE hm.master_hotel_id = :master_hotel_id
                ORDER BY hm.confidence_tier, s.supplier_name;
                """
            ),
            {"master_hotel_id": row["master_hotel_id"]}
        )

        history = await self.session.execute(
            text(
                """
                SELECT event, related_id, detail, created_at
                FROM master_hotel_lifecycle
                WHERE public_id = :public_id
                ORDER BY created_at DESC, id DESC
                LIMIT 50;
                """
            ),
            {"public_id": resolved}
        )

        detail = dict(row)
        detail["requested_public_id"] = public_id
        detail["was_redirected"] = (public_id != resolved)
        detail["members"] = [dict(r) for r in members.mappings().all()]
        detail["history"] = [dict(r) for r in history.mappings().all()]

        return detail

    # ── split ───────────────────────────────────────────────────────────────

    async def split_master(
        self,
        public_id: str,
        supplier_row_ids: list[int],
        reason: str,
        actor: str = "reviewer",
    ):
        """
        Move the listed supplier rows out into a master of their own.

        The original id keeps the remainder, because consumers holding it more
        often meant the hotel that stayed.
        """
        resolved = await self.identity.resolve(public_id)

        if resolved is None:
            return {"ok": False, "error": "Unknown public id"}

        if not supplier_row_ids:
            return {"ok": False, "error": "No records selected"}

        detail = await self.get_master_detail(resolved)
        member_ids = {m["supplier_hotel_row_id"] for m in detail["members"]}

        moving = [r for r in supplier_row_ids if r in member_ids]
        staying = sorted(member_ids - set(moving))

        if not moving:
            return {"ok": False, "error": "None of those records belong to this master"}

        if not staying:
            return {
                "ok": False,
                "error": "Cannot move every record — that is not a split. "
                         "Deprecate the master instead if it is not a real hotel.",
            }

        seed_row_id = min(moving)

        seed = await self.session.execute(
            text(
                """
                SELECT hotel_name, core_name, strict_name, address, city, state, country,
                       postal_code, star_rating, latitude, longitude, geo_location
                FROM supplier_hotels WHERE id = :row_id;
                """
            ),
            {"row_id": seed_row_id}
        )
        seed_row = seed.mappings().first()

        created = await self.session.execute(
            text(
                """
                INSERT INTO master_hotels
                    (hotel_name, normalized_name, core_name, strict_name, address,
                     city, state, country, postal_code, star_rating, latitude,
                     longitude, geo_location)
                VALUES
                    (:hotel_name, NULL, :core_name, :strict_name, :address,
                     :city, :state, :country, :postal_code, :star_rating, :latitude,
                     :longitude, :geo_location)
                RETURNING master_hotel_id;
                """
            ),
            dict(seed_row)
        )
        new_master_id = created.scalar_one()

        new_public_id = await self.identity.register_master(
            new_master_id, seed_row_id, force_new=True
        )

        # Repoint the moving rows' mappings and identity anchors.
        await self.session.execute(
            text(
                """
                UPDATE hotel_mappings
                SET master_hotel_id = :new_master_id,
                    is_manual_verified = TRUE
                WHERE supplier_hotel_row_id = ANY(:row_ids);
                """
            ),
            {"new_master_id": new_master_id, "row_ids": moving}
        )

        await self.session.execute(
            text(
                """
                UPDATE master_hotel_anchor
                SET public_id = :new_public_id
                WHERE supplier_hotel_row_id = ANY(:row_ids);
                """
            ),
            {"new_public_id": new_public_id, "row_ids": moving}
        )

        # The decision that makes the split stick.
        pairs = 0
        for moved in moving:
            for kept in staying:
                a, b = sorted((moved, kept))
                await self.session.execute(
                    text(
                        """
                        INSERT INTO master_non_merge_assertion
                            (row_id_a, row_id_b, reason, asserted_by)
                        VALUES (:a, :b, :reason, :actor)
                        ON CONFLICT (row_id_a, row_id_b) DO NOTHING;
                        """
                    ),
                    {"a": a, "b": b, "reason": reason, "actor": actor}
                )
                pairs += 1

        await self.identity._log(
            resolved, "SPLIT", new_public_id,
            f"{len(moving)} record(s) moved to {new_public_id} by {actor}: {reason}"
        )
        await self.identity._log(
            new_public_id, "SPLIT", resolved,
            f"Split out of {resolved} by {actor}: {reason}"
        )

        await self.session.commit()

        logger.info(
            "Split %s -> %s (%d records, %d non-merge pairs) by %s",
            resolved, new_public_id, len(moving), pairs, actor
        )

        return {
            "ok": True,
            "original_public_id": resolved,
            "new_public_id": new_public_id,
            "records_moved": len(moving),
            "records_kept": len(staying),
            "non_merge_pairs_recorded": pairs,
        }

    # ── merge ───────────────────────────────────────────────────────────────

    async def replay_merge_decisions(self) -> dict:
        """
        Re-apply recorded merges after a rebuild.

        A reset re-registers masters from their anchors, so two ids a reviewer
        folded together come back as two separate masters and the merge is
        silently undone — the same failure approvals had, by a different route.
        Merges are the entire output of the Known Splits screen, so without this
        that work survives exactly until the next run.

        Replayed oldest first, so a chain (B into A, then C into B) lands the
        same way it was decided. A decision whose ids already resolve to one
        master is a no-op, which is the common case on a run with no reset.

        `force` is deliberately not set: a merge the reviewer later reversed with
        a split leaves a non-merge assertion, and that is a newer decision than
        this one. Replay restores what a rebuild dropped; it does not overrule
        anybody.
        """
        decisions = await self.session.execute(
            text(
                """
                SELECT retired_public_id, surviving_public_id, reason, decided_by
                FROM master_merge_decision
                ORDER BY id;
                """
            )
        )

        reapplied = already = skipped = 0

        for decision in decisions.mappings().all():
            retired = await self.identity.resolve(decision["retired_public_id"])
            surviving = await self.identity.resolve(decision["surviving_public_id"])

            if retired is None or surviving is None:
                skipped += 1
                continue

            if retired == surviving:
                already += 1
                continue

            result = await self.merge_masters(
                retired,
                surviving,
                decision["reason"] or "Replayed reviewer merge",
                decision["decided_by"] or "reviewer",
            )

            if result.get("ok"):
                reapplied += 1
            else:
                skipped += 1
                logger.info(
                    "Could not replay merge %s into %s: %s",
                    decision["retired_public_id"],
                    decision["surviving_public_id"],
                    result.get("error"),
                )

        if reapplied or skipped:
            logger.info(
                "Merge decisions replayed: %d re-applied, %d already merged, "
                "%d skipped",
                reapplied, already, skipped
            )

        return {
            "reapplied": reapplied,
            "already_merged": already,
            "skipped": skipped,
        }

    async def merge_masters(
        self,
        source_public_id: str,
        target_public_id: str,
        reason: str,
        actor: str = "reviewer",
        force: bool = False,
    ):
        """
        Fold one master into another: the reviewer's verdict that two ids are
        one property.

        This is the action both review queues were missing. A provisional master
        that turned out to be a hotel we already had, and an exact-name pair the
        1 km candidate radius split apart, are both resolved here.

        Which public id survives is not the reviewer's choice and deliberately
        so: MasterIdentityService.merge always retires the younger into the
        older, so the outcome does not depend on which of the two the reviewer
        happened to open first. The *data* follows the reviewer — the surviving
        id is repointed at the master they chose to keep.
        """
        source = await self.identity.resolve(source_public_id)
        target = await self.identity.resolve(target_public_id)

        if source is None:
            return {"ok": False, "error": f"Unknown public id {source_public_id}"}

        if target is None:
            return {"ok": False, "error": f"Unknown public id {target_public_id}"}

        if source == target:
            return {"ok": False, "error": "Those two ids are already the same master"}

        if not reason or len(reason.strip()) < 3:
            return {"ok": False, "error": "A reason is required"}

        rows = await self.session.execute(
            text(
                """
                SELECT r.public_id, r.status, r.master_hotel_id
                FROM master_hotel_registry r
                WHERE r.public_id IN (:source, :target);
                """
            ),
            {"source": source, "target": target}
        )

        registry = {row["public_id"]: dict(row) for row in rows.mappings().all()}
        source_master_id = registry.get(source, {}).get("master_hotel_id")
        target_master_id = registry.get(target, {}).get("master_hotel_id")

        if source_master_id is None or target_master_id is None:
            return {"ok": False, "error": "One of those ids has no master hotel behind it"}

        # A reviewer previously stated these are different hotels. Silently
        # merging over that decision would make splitting pointless work, so it
        # takes an explicit override — and the override clears the assertion
        # rather than leaving a contradiction in the table.
        blocked = await self.session.execute(
            text(
                """
                SELECT count(*) AS pairs
                FROM master_non_merge_assertion nm
                WHERE EXISTS (
                        SELECT 1 FROM hotel_mappings a
                        WHERE a.master_hotel_id = :source_master_id
                          AND a.supplier_hotel_row_id IN (nm.row_id_a, nm.row_id_b)
                      )
                  AND EXISTS (
                        SELECT 1 FROM hotel_mappings b
                        WHERE b.master_hotel_id = :target_master_id
                          AND b.supplier_hotel_row_id IN (nm.row_id_a, nm.row_id_b)
                      );
                """
            ),
            {"source_master_id": source_master_id, "target_master_id": target_master_id}
        )

        assertion_pairs = blocked.scalar() or 0

        if assertion_pairs and not force:
            return {
                "ok": False,
                "error": (
                    f"A reviewer previously split these records apart "
                    f"({assertion_pairs} non-merge assertion(s)). Re-merging "
                    f"requires force=true and overrides that decision."
                ),
                "non_merge_assertions": assertion_pairs,
            }

        # Two records from one supplier inside a single master is the pipeline's
        # highest-precision false-merge signal. Not blocked — the reviewer may
        # know the supplier double-listed one property — but never silent.
        overlap = await self.session.execute(
            text(
                """
                SELECT a.supplier_name
                FROM hotel_mappings a
                JOIN hotel_mappings b ON b.supplier_name = a.supplier_name
                WHERE a.master_hotel_id = :source_master_id
                  AND b.master_hotel_id = :target_master_id
                GROUP BY a.supplier_name;
                """
            ),
            {"source_master_id": source_master_id, "target_master_id": target_master_id}
        )

        overlapping_suppliers = [row[0] for row in overlap.fetchall()]

        moved = await self.session.execute(
            text(
                """
                UPDATE hotel_mappings
                SET master_hotel_id = :target_master_id,
                    is_manual_verified = TRUE
                WHERE master_hotel_id = :source_master_id
                RETURNING supplier_hotel_row_id;
                """
            ),
            {"source_master_id": source_master_id, "target_master_id": target_master_id}
        )

        moved_rows = [row[0] for row in moved.fetchall()]

        if assertion_pairs and force:
            await self.session.execute(
                text(
                    """
                    DELETE FROM master_non_merge_assertion nm
                    WHERE EXISTS (
                            SELECT 1 FROM hotel_mappings a
                            WHERE a.master_hotel_id = :target_master_id
                              AND a.supplier_hotel_row_id IN (nm.row_id_a, nm.row_id_b)
                          );
                    """
                ),
                {"target_master_id": target_master_id}
            )

        surviving = await self.identity.merge(source, target)

        # The surviving id must point at the master the reviewer kept. When the
        # source id was the older of the two it survives, and without this it
        # would still point at the master_hotels row about to be deleted.
        await self.session.execute(
            text(
                """
                UPDATE master_hotel_registry
                SET master_hotel_id = :target_master_id,
                    superseded_by = NULL,
                    last_seen = NOW()
                WHERE public_id = :surviving;
                """
            ),
            {"target_master_id": target_master_id, "surviving": surviving}
        )

        # The emptied master row has to go. Left in place it keeps its geo and
        # name indexes and goes on attracting new supplier records, recreating
        # the duplicate the reviewer just resolved.
        await self.session.execute(
            text("DELETE FROM hotel_embeddings WHERE master_hotel_id = :master_id;"),
            {"master_id": source_master_id}
        )

        await self.session.execute(
            text("DELETE FROM master_hotels WHERE master_hotel_id = :master_id;"),
            {"master_id": source_master_id}
        )

        await self.session.execute(
            text(
                """
                INSERT INTO master_merge_decision
                    (retired_public_id, surviving_public_id, reason, decided_by,
                     records_moved)
                VALUES (:retired, :surviving, :reason, :actor, :moved);
                """
            ),
            {
                "retired": source if surviving == target else target,
                "surviving": surviving,
                "reason": reason,
                "actor": actor,
                "moved": len(moved_rows),
            }
        )

        await self.identity._log(
            surviving, "MERGED_IN",
            source if surviving == target else target,
            f"{len(moved_rows)} record(s) absorbed by {actor}: {reason}"
        )

        # The absorbed records may be the second supplier this master needed.
        await self.identity.promote_if_corroborated(target_master_id)

        await self.session.commit()

        logger.info(
            "Merged %s into %s (%d records) by %s",
            source, target, len(moved_rows), actor
        )

        return {
            "ok": True,
            "surviving_public_id": surviving,
            "retired_public_id": source if surviving == target else target,
            "records_moved": len(moved_rows),
            "overlapping_suppliers": overlapping_suppliers,
            "non_merge_assertions_overridden": assertion_pairs if force else 0,
        }

    # ── deprecate / reactivate ──────────────────────────────────────────────

    async def deprecate_master(
        self,
        public_id: str,
        reason: str,
        case: str = "Closed",
        actor: str = "reviewer",
    ):
        """
        Mark a master not bookable. The id is never deleted — a consumer asking
        about it must hear "closed", not receive an error.

        `case` distinguishes Closed (property shut) from Dormant (still exists,
        no supplier currently lists it — auto-reactivates if a feed returns it).
        """
        resolved = await self.identity.resolve(public_id)

        if resolved is None:
            return {"ok": False, "error": "Unknown public id"}

        if case not in ("Closed", "Dormant"):
            return {"ok": False, "error": "case must be Closed or Dormant"}

        await self.session.execute(
            text(
                """
                UPDATE master_hotel_registry
                SET status = :status,
                    deprecation_reason = :reason,
                    deprecated_by = :actor,
                    last_seen = NOW()
                WHERE public_id = :public_id;
                """
            ),
            {
                "status": "Deprecated" if case == "Closed" else "Dormant",
                "reason": reason,
                "actor": actor,
                "public_id": resolved,
            }
        )

        await self.identity._log(
            resolved, "DEPRECATED", None, f"{case} by {actor}: {reason}"
        )

        await self.session.commit()

        return {"ok": True, "public_id": resolved, "status": case}

    async def reactivate_master(self, public_id: str, reason: str, actor: str = "reviewer"):
        resolved = await self.identity.resolve(public_id)

        if resolved is None:
            return {"ok": False, "error": "Unknown public id"}

        await self.session.execute(
            text(
                """
                UPDATE master_hotel_registry
                SET status = 'Active',
                    deprecation_reason = NULL,
                    deprecated_by = NULL,
                    last_seen = NOW()
                WHERE public_id = :public_id;
                """
            ),
            {"public_id": resolved}
        )

        await self.identity._log(
            resolved, "REACTIVATED", None, f"Reactivated by {actor}: {reason}"
        )

        await self.session.commit()

        return {"ok": True, "public_id": resolved, "status": "Active"}

    async def search_masters(self, query: str, limit: int = 50):
        result = await self.session.execute(
            text(
                """
                SELECT r.public_id, r.status, m.hotel_name, m.city, m.country,
                       (SELECT count(*) FROM hotel_mappings hm
                         WHERE hm.master_hotel_id = m.master_hotel_id) AS supplier_count
                FROM master_hotel_registry r
                JOIN master_hotels m ON m.master_hotel_id = r.master_hotel_id
                WHERE r.public_id ILIKE :like
                   OR m.hotel_name ILIKE :like
                   OR m.city ILIKE :like
                ORDER BY similarity(lower(m.hotel_name), lower(:raw)) DESC
                LIMIT :limit;
                """
            ),
            {"like": f"%{query}%", "raw": query, "limit": limit}
        )
        return [dict(r) for r in result.mappings().all()]
