import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

PUBLIC_ID_PREFIX = "HBM"


class MasterIdentityService:
    """
    Stable public identity for master hotels.

    `master_hotel_id` is an internal auto-increment key: it is reassigned by any
    full rebuild, so it must never be published. Consumers are given `public_id`
    (HBM-00000001), which survives rebuilds and carries merge/deprecation
    history.

    Stability comes from the *anchor* table, which remembers which supplier rows
    have ever belonged to which public id and is never truncated. On a rebuild,
    a master seeded by a previously-seen supplier row reclaims its original
    public id rather than minting a new one.

    When a supplier row that already belongs to public id A is mapped into a
    master carrying public id B, the two ids describe one property. The younger
    is marked Merged into the older and its consumers keep resolving, so a
    cached id is never silently invalidated.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def _mint_public_id(self) -> str:
        result = await self.session.execute(
            text("SELECT nextval('master_public_id_seq');")
        )
        return f"{PUBLIC_ID_PREFIX}-{result.scalar():08d}"

    async def anchor_for_row(self, supplier_hotel_row_id: int) -> str | None:
        """
        The public id this supplier row belonged to in an earlier run, resolved
        forward through any merges. Always returns a live id, never a retired
        one.
        """
        result = await self.session.execute(
            text(
                """
                SELECT public_id
                FROM master_hotel_anchor
                WHERE supplier_hotel_row_id = :row_id;
                """
            ),
            {"row_id": supplier_hotel_row_id}
        )

        stored = result.scalar()

        return await self.resolve(stored) if stored else None

    async def public_id_for_master(self, master_hotel_id: int) -> str | None:
        result = await self.session.execute(
            text(
                """
                SELECT public_id
                FROM master_hotel_registry
                WHERE master_hotel_id = :master_hotel_id
                  -- Provisional counts here. A provisional master is matchable,
                  -- just not publishable: excluding it would leave the second
                  -- supplier for a hotel unable to attach to the master the
                  -- first one seeded, which is the whole point of keeping the
                  -- row rather than deferring its creation.
                  AND status IN ('Active', 'Provisional')
                LIMIT 1;
                """
            ),
            {"master_hotel_id": master_hotel_id}
        )
        return result.scalar()

    async def _log(self, public_id, event, related_id=None, detail=None):
        await self.session.execute(
            text(
                """
                INSERT INTO master_hotel_lifecycle
                    (public_id, event, related_id, detail)
                VALUES (:public_id, :event, :related_id, :detail);
                """
            ),
            {
                "public_id": public_id,
                "event": event,
                "related_id": related_id,
                "detail": detail,
            }
        )

    async def register_master(
        self,
        master_hotel_id: int,
        seeding_supplier_row_id: int,
        force_new: bool = False,
        status: str = "Provisional",
    ) -> str:
        """
        Assign a public id to a newly created master.

        Reclaims the id this supplier row held previously, so a rebuild produces
        the same public ids for the same data.

        `force_new` is for splits: the record is deliberately being moved to a
        *different* property, so reclaiming its old id would defeat the purpose
        and leave both halves sharing one identity.

        `status` defaults to Provisional — a master seeded by a single supplier
        row is one supplier's claim that a property exists, and nothing has yet
        corroborated it. It is matchable immediately (later suppliers need
        something to attach to) but stays out of every export until a second
        distinct supplier arrives or a reviewer confirms it. Callers acting on
        an explicit human decision pass status='Active'.
        """
        existing = None if force_new else await self.anchor_for_row(seeding_supplier_row_id)

        if existing is not None:
            # An id can only describe one master. Two supplier rows that shared
            # a public id in an earlier run — because they were merged then —
            # can be split across two masters by a later run, and both then try
            # to reclaim it. An unconditional UPDATE handed it to whichever
            # registered last and left the other master with no public id at
            # all: unpublishable, blank in exports, invisible to any consumer.
            #
            # The claim must be conditional *inside* the UPDATE, not checked
            # before it. Four workers run concurrently in separate transactions,
            # so a SELECT beforehand reads a snapshot that does not yet contain
            # the other worker's uncommitted registration — both see the id as
            # free and both take it. Making the condition part of the UPDATE
            # makes it a row lock instead: the second writer blocks until the
            # first commits and then re-evaluates against the committed value,
            # so exactly one of them can win.
            #
            # Zero rows back means someone else holds it. The data has changed
            # its mind — these are two properties now — so the one that did not
            # get there first becomes a new identity, exactly as `force_new`
            # treats a deliberate split.
            claimed = await self.session.execute(
                text(
                    """
                    UPDATE master_hotel_registry
                    SET master_hotel_id = :master_hotel_id,
                        -- A rebuild must not silently re-publish an id that a
                        -- reviewer never confirmed, so the reclaimed row goes
                        -- back to whatever this registration is entitled to.
                        status = :status,
                        superseded_by = NULL,
                        last_seen = NOW()
                    WHERE public_id = :public_id
                      AND (master_hotel_id IS NULL
                           OR master_hotel_id = :master_hotel_id)
                    RETURNING public_id;
                    """
                ),
                {
                    "master_hotel_id": master_hotel_id,
                    "public_id": existing,
                    "status": status,
                }
            )

            if claimed.scalar() is None:
                logger.info(
                    "Public id %s is already held by another master; minting a "
                    "new id for master %s rather than orphaning one of them",
                    existing, master_hotel_id
                )
                existing = None

        if existing is not None:

            await self._log(
                existing, "REUSED", None,
                f"Reclaimed on rebuild for master_hotel_id={master_hotel_id} "
                f"as {status}"
            )

            return existing

        public_id = await self._mint_public_id()

        await self.session.execute(
            text(
                """
                INSERT INTO master_hotel_registry (public_id, master_hotel_id, status)
                VALUES (:public_id, :master_hotel_id, :status);
                """
            ),
            {
                "public_id": public_id,
                "master_hotel_id": master_hotel_id,
                "status": status,
            }
        )

        await self._attach_anchor(seeding_supplier_row_id, public_id)
        await self._log(
            public_id, "CREATED", None,
            f"master_hotel_id={master_hotel_id} status={status}"
        )

        return public_id

    async def promote_if_corroborated(self, master_hotel_id: int) -> str | None:
        """
        Publish a provisional master once a second distinct supplier joins it.

        Corroboration is the whole test: two independent feeds listing the same
        property is the evidence that it exists and is one hotel. Records from
        one supplier cannot corroborate each other — the pipeline already treats
        two rows from one feed inside a master as a probable false merge.

        Returns the public id if it was promoted, otherwise None.
        """
        result = await self.session.execute(
            text(
                """
                UPDATE master_hotel_registry r
                SET status = 'Active',
                    confirmed_at = NOW(),
                    confirmed_by = 'pipeline',
                    confirm_reason = 'Corroborated by a second supplier',
                    last_seen = NOW()
                WHERE r.master_hotel_id = :master_hotel_id
                  AND r.status = 'Provisional'
                  AND (
                      SELECT count(DISTINCT hm.supplier_name)
                      FROM hotel_mappings hm
                      WHERE hm.master_hotel_id = :master_hotel_id
                  ) >= 2
                RETURNING r.public_id;
                """
            ),
            {"master_hotel_id": master_hotel_id}
        )

        public_id = result.scalar()

        if public_id is not None:
            await self._log(
                public_id, "CONFIRMED", None,
                "Promoted from Provisional — corroborated by a second supplier"
            )
            logger.info("Master identity: %s confirmed by corroboration", public_id)

        return public_id

    async def confirm_master(
        self,
        public_id: str,
        reason: str,
        actor: str = "reviewer",
    ) -> dict:
        """
        Publish a provisional master on a reviewer's authority alone.

        The legitimate case for a single-supplier master: a property only one
        supplier carries. Common enough that without this the provisional queue
        would never drain.
        """
        resolved = await self.resolve(public_id)

        if resolved is None:
            return {"ok": False, "error": "Unknown public id"}

        result = await self.session.execute(
            text(
                """
                UPDATE master_hotel_registry
                SET status = 'Active',
                    confirmed_at = NOW(),
                    confirmed_by = :actor,
                    confirm_reason = :reason,
                    last_seen = NOW()
                WHERE public_id = :public_id
                  AND status = 'Provisional'
                RETURNING public_id;
                """
            ),
            {"public_id": resolved, "actor": actor, "reason": reason}
        )

        if result.scalar() is None:
            return {
                "ok": False,
                "error": f"{resolved} is not awaiting confirmation",
            }

        await self._log(
            resolved, "CONFIRMED", None, f"Confirmed by {actor}: {reason}"
        )

        await self.session.commit()

        return {"ok": True, "public_id": resolved, "status": "Active"}

    async def _attach_anchor(self, supplier_hotel_row_id: int, public_id: str):
        await self.session.execute(
            text(
                """
                INSERT INTO master_hotel_anchor (supplier_hotel_row_id, public_id)
                VALUES (:row_id, :public_id)
                ON CONFLICT (supplier_hotel_row_id)
                DO UPDATE SET public_id = EXCLUDED.public_id;
                """
            ),
            {"row_id": supplier_hotel_row_id, "public_id": public_id}
        )

    async def attach_to_master(
        self,
        master_hotel_id: int,
        supplier_hotel_row_id: int,
    ) -> str | None:
        """
        Record that a supplier row now belongs to this master.

        If the row previously belonged to a different public id, the two ids
        describe the same property and are merged — younger into older — rather
        than one being discarded.
        """
        target_public_id = await self.public_id_for_master(master_hotel_id)

        if target_public_id is None:
            return None

        previous = await self.anchor_for_row(supplier_hotel_row_id)

        if previous is not None and previous != target_public_id:
            # Two ids may only be merged when they really do describe one
            # property. If `previous` is currently the identity of a *different*
            # live master, they describe two properties that this run has kept
            # apart, and this row is simply moving between them — merging would
            # retire one id and re-point the survivor here, leaving the master it
            # came from with no public id at all. That was the source of the
            # orphans that survived the fix to register_master.
            #
            # The test is the reservation itself rather than a preceding SELECT:
            # four workers register concurrently, so a read would miss another
            # transaction's uncommitted claim. As part of the UPDATE it is a row
            # lock — the second writer waits, re-evaluates against the committed
            # value, and comes back empty.
            reserved = await self.session.execute(
                text(
                    """
                    UPDATE master_hotel_registry
                    SET master_hotel_id = :master_hotel_id,
                        -- Revive a retired id without publishing it. Forcing
                        -- 'Active' here would let a merge quietly confirm a
                        -- master no reviewer ever looked at; corroboration
                        -- below is what promotes it.
                        status = CASE WHEN status = 'Active'
                                      THEN 'Active' ELSE 'Provisional' END,
                        superseded_by = NULL,
                        last_seen = NOW()
                    WHERE public_id = :public_id
                      AND (master_hotel_id IS NULL
                           OR master_hotel_id = :master_hotel_id)
                    RETURNING public_id;
                    """
                ),
                {
                    "master_hotel_id": master_hotel_id,
                    "public_id": previous,
                }
            )

            if reserved.scalar() is not None:
                # Both ids now point at this master, so retiring one into the
                # other is sound. Whichever survives already references this
                # master — `previous` through the reservation above, the target
                # because it was this master's id to begin with — so no further
                # rebinding is needed, and none can strand a third master.
                target_public_id = await self.merge(previous, target_public_id)
            else:
                logger.info(
                    "Public id %s still identifies another master; row %s moves "
                    "to %s without merging the two ids",
                    previous, supplier_hotel_row_id, target_public_id
                )

        await self._attach_anchor(supplier_hotel_row_id, target_public_id)

        # This row may be the second supplier on the master, which is what makes
        # a provisional master publishable.
        await self.promote_if_corroborated(master_hotel_id)

        return target_public_id

    async def merge(self, from_public_id: str, into_public_id: str) -> str:
        """
        Retire one public id into another and return the survivor.

        The older id always survives, so the outcome does not depend on the
        order rows happen to be processed in. The retired id is never deleted —
        it keeps resolving forward, so a consumer holding it is never broken.
        """
        older, younger = sorted([from_public_id, into_public_id])

        if older == younger:
            return older

        await self.session.execute(
            text(
                """
                UPDATE master_hotel_registry
                SET status = 'Merged',
                    superseded_by = :older,
                    master_hotel_id = NULL,
                    last_seen = NOW()
                WHERE public_id = :younger
                  AND status <> 'Merged';
                """
            ),
            {"older": older, "younger": younger}
        )

        # Any row still anchored to the retired id moves to the survivor.
        await self.session.execute(
            text(
                """
                UPDATE master_hotel_anchor
                SET public_id = :older
                WHERE public_id = :younger;
                """
            ),
            {"older": older, "younger": younger}
        )

        await self._log(
            younger, "MERGED", older,
            f"{younger} merged into {older}"
        )

        logger.info("Master identity: %s merged into %s", younger, older)

        return older

    async def resolve(self, public_id: str) -> str | None:
        """
        Follow the merge chain to the currently active id.

        A consumer holding a retired id always gets a usable answer — the
        failure mode this replaces was a cached id silently pointing at a
        different hotel after a rebuild.
        """
        current = public_id

        for _ in range(16):
            result = await self.session.execute(
                text(
                    """
                    SELECT status, superseded_by
                    FROM master_hotel_registry
                    WHERE public_id = :public_id;
                    """
                ),
                {"public_id": current}
            )

            row = result.mappings().first()

            if row is None:
                return None

            if row["status"] != "Merged" or row["superseded_by"] is None:
                return current

            current = row["superseded_by"]

        logger.warning("Master identity: merge chain too deep from %s", public_id)
        return current
