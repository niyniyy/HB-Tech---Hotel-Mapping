import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.publication_gate import publishable_sql

logger = logging.getLogger(__name__)


class AccuracyEvaluationService:
    """
    Score the pipeline against a reference mapping.

    Every other quality number in this system measures internal consistency —
    zero same-supplier collisions, zero orphans, N duplicate pairs. None of them
    can tell you whether a mapping is *right*, and for most of this project the
    accuracy claim rested on 66 hand-adjudicated pairs, which bounds the
    false-positive rate below 4.5% at 95% confidence rather than at zero.

    A reference mapping — Vervotech's, or any hand-built one — turns that into a
    measurement. Two failures matter and they are not symmetric:

    FALSE MERGE   two different hotels in one of our masters. Unrecoverable in a
                  booking path: a guest is sent to the wrong property. This is
                  the number that must stay near zero.

    SPLIT         one hotel spread across several of our masters. Costs
                  coverage, and a reviewer can merge it later.

    The whole system is tuned to trade the second for the first, so the two are
    always reported together — a change that improves one at the other's expense
    is the normal case, not an anomaly, and must be visible as such.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def has_reference(self) -> bool:
        result = await self.session.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'reference_mapping'
                );
                """
            )
        )
        if not result.scalar():
            return False

        rows = await self.session.execute(
            text("SELECT count(*) FROM reference_mapping;")
        )
        return (rows.scalar() or 0) > 0

    async def gate_status(self) -> dict:
        """
        The release gate: is anything wrong in what we actually publish?

        A false merge sends a guest to the wrong hotel and cannot be undone
        downstream, so the rule the whole tier design exists to enforce is "zero
        false merges reach a consumer". This reduces the full evaluation to the
        one boolean that answers it, cheaply enough to run after every pipeline
        batch and to poll from the dashboard.

        `passing` is true when no reference is loaded, because the gate cannot
        fail on evidence it does not have — the absence is reported in `reason`
        rather than dressed up as a pass.
        """
        if not await self.has_reference():
            return {
                "passing": True,
                "measured": False,
                "published_false_merges": 0,
                "reason": "No reference mapping loaded — accuracy is unmeasured.",
            }

        result = await self.session.execute(
            text(
                f"""
                WITH em AS (
                    SELECT DISTINCT r.reference_id, hm.master_hotel_id
                    FROM reference_mapping r
                    JOIN supplier_hotels s
                      ON s.supplier_name = r.supplier_name
                     AND s.supplier_hotel_id = r.supplier_hotel_id
                    JOIN hotel_mappings hm ON hm.supplier_hotel_row_id = s.id
                    WHERE {publishable_sql('hm')}
                )
                SELECT count(*) FILTER (WHERE refs > 1)      AS false_merges,
                       count(*)                              AS masters_published,
                       count(DISTINCT reference_id)          AS hotels_measured
                FROM (SELECT master_hotel_id,
                             count(DISTINCT reference_id) AS refs,
                             min(reference_id)            AS reference_id
                      FROM em GROUP BY 1) x;
                """
            )
        )
        row = dict(result.mappings().first())
        fm = row["false_merges"]

        return {
            "passing": fm == 0,
            "measured": True,
            "published_false_merges": fm,
            "masters_published": row["masters_published"],
            "hotels_measured": row["hotels_measured"],
            "reason": (
                "No wrong merges in published data."
                if fm == 0
                else f"{fm} wrong merge(s) reached the published set."
            ),
        }

    async def evaluate(self) -> dict:
        if not await self.has_reference():
            return {
                "available": False,
                "message": (
                    "No reference mapping loaded. Import one into "
                    "reference_mapping to measure accuracy."
                ),
            }

        # Which of our masters each reference hotel's records landed in. A
        # reference pair can match more than one supplier row when the supplier
        # reused an id, so this is DISTINCT rather than a plain join.
        await self.session.execute(text("DROP TABLE IF EXISTS _eval_map;"))
        await self.session.execute(
            text(
                """
                CREATE TEMP TABLE _eval_map AS
                SELECT DISTINCT r.reference_id, hm.master_hotel_id
                FROM reference_mapping r
                JOIN supplier_hotels s
                  ON s.supplier_name = r.supplier_name
                 AND s.supplier_hotel_id = r.supplier_hotel_id
                JOIN hotel_mappings hm ON hm.supplier_hotel_row_id = s.id;
                """
            )
        )

        coverage = await self.session.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM reference_mapping)                     AS reference_pairs,
                  (SELECT count(DISTINCT reference_id) FROM reference_mapping)  AS reference_hotels,
                  (SELECT count(*) FROM reference_mapping r
                     JOIN supplier_hotels s
                       ON s.supplier_name = r.supplier_name
                      AND s.supplier_hotel_id = r.supplier_hotel_id)            AS pairs_present,
                  (SELECT count(DISTINCT reference_id) FROM _eval_map)          AS hotels_evaluated;
                """
            )
        )

        precision = await self.session.execute(
            text(
                """
                SELECT count(*)                            AS masters_evaluated,
                       count(*) FILTER (WHERE refs = 1)    AS clean,
                       count(*) FILTER (WHERE refs > 1)    AS false_merges
                FROM (SELECT master_hotel_id, count(DISTINCT reference_id) AS refs
                      FROM _eval_map GROUP BY 1) x;
                """
            )
        )

        recall = await self.session.execute(
            text(
                """
                SELECT count(*)                              AS hotels_evaluated,
                       count(*) FILTER (WHERE masters = 1)   AS intact,
                       count(*) FILTER (WHERE masters > 1)   AS split
                FROM (SELECT reference_id, count(DISTINCT master_hotel_id) AS masters
                      FROM _eval_map GROUP BY 1) x;
                """
            )
        )

        spread = await self.session.execute(
            text(
                """
                SELECT masters AS masters_per_hotel, count(*) AS hotels
                FROM (SELECT reference_id, count(DISTINCT master_hotel_id) AS masters
                      FROM _eval_map GROUP BY 1) x
                GROUP BY 1 ORDER BY 1;
                """
            )
        )

        cov = dict(coverage.mappings().first())
        prec = dict(precision.mappings().first())
        rec = dict(recall.mappings().first())

        masters_evaluated = prec["masters_evaluated"] or 1
        hotels_evaluated = rec["hotels_evaluated"] or 1

        # The same measurement restricted to what actually reaches a consumer.
        # A held TIER3 merge being wrong costs nothing until it is published, so
        # this is the number the product is accountable for.
        published = await self.session.execute(
            text(
                f"""
                WITH em AS (
                    SELECT DISTINCT r.reference_id, hm.master_hotel_id
                    FROM reference_mapping r
                    JOIN supplier_hotels s
                      ON s.supplier_name = r.supplier_name
                     AND s.supplier_hotel_id = r.supplier_hotel_id
                    JOIN hotel_mappings hm ON hm.supplier_hotel_row_id = s.id
                    WHERE {publishable_sql('hm')}
                )
                SELECT count(*)                         AS masters_published,
                       count(*) FILTER (WHERE refs > 1) AS false_merges
                FROM (SELECT master_hotel_id, count(DISTINCT reference_id) AS refs
                      FROM em GROUP BY 1) x;
                """
            )
        )
        pub = dict(published.mappings().first())
        pub_total = pub["masters_published"] or 1

        held = await self.session.execute(
            text(
                f"""
                SELECT count(*) FILTER (WHERE NOT {publishable_sql('hm')}) AS held,
                       count(*) FILTER (WHERE {publishable_sql('hm')})     AS published
                FROM hotel_mappings hm;
                """
            )
        )

        return {
            "available": True,
            "coverage": cov,
            "published_only": {
                **pub,
                "pct_clean": round(100.0 * (pub["masters_published"] - pub["false_merges"])
                                   / pub_total, 2),
                **dict(held.mappings().first()),
            },
            "precision": {
                **prec,
                "pct_clean": round(100.0 * prec["clean"] / masters_evaluated, 2),
            },
            "recall": {
                **rec,
                "pct_intact": round(100.0 * rec["intact"] / hotels_evaluated, 2),
            },
            "split_spread": [dict(r) for r in spread.mappings().all()],
        }

    async def false_merges(self, limit: int = 100):
        """Each master holding more than one reference hotel, with the names."""
        if not await self.has_reference():
            return []

        result = await self.session.execute(
            text(
                """
                WITH em AS (
                    SELECT DISTINCT r.reference_id, hm.master_hotel_id
                    FROM reference_mapping r
                    JOIN supplier_hotels s
                      ON s.supplier_name = r.supplier_name
                     AND s.supplier_hotel_id = r.supplier_hotel_id
                    JOIN hotel_mappings hm ON hm.supplier_hotel_row_id = s.id
                ),
                bad AS (
                    SELECT master_hotel_id FROM em
                    GROUP BY 1 HAVING count(DISTINCT reference_id) > 1
                )
                SELECT b.master_hotel_id,
                       reg.public_id,
                       m.hotel_name AS our_master,
                       m.city,
                       em.reference_id,
                       (SELECT string_agg(DISTINCT s2.hotel_name, ' | ')
                          FROM reference_mapping r2
                          JOIN supplier_hotels s2
                            ON s2.supplier_name = r2.supplier_name
                           AND s2.supplier_hotel_id = r2.supplier_hotel_id
                         WHERE r2.reference_id = em.reference_id) AS reference_hotel_names
                FROM bad b
                JOIN em ON em.master_hotel_id = b.master_hotel_id
                JOIN master_hotels m ON m.master_hotel_id = b.master_hotel_id
                LEFT JOIN master_hotel_registry reg
                       ON reg.master_hotel_id = b.master_hotel_id
                ORDER BY b.master_hotel_id, em.reference_id
                LIMIT :limit;
                """
            ),
            {"limit": limit},
        )

        return [dict(r) for r in result.mappings().all()]

    async def splits(self, limit: int = 200):
        """
        Reference hotels spread across several masters, worst first.

        `in_review` separates the two populations that need different work: a
        split with a record already queued is waiting on a reviewer, one without
        is a case the pipeline never even doubted.
        """
        if not await self.has_reference():
            return []

        result = await self.session.execute(
            text(
                """
                WITH em AS (
                    SELECT DISTINCT r.reference_id, hm.master_hotel_id
                    FROM reference_mapping r
                    JOIN supplier_hotels s
                      ON s.supplier_name = r.supplier_name
                     AND s.supplier_hotel_id = r.supplier_hotel_id
                    JOIN hotel_mappings hm ON hm.supplier_hotel_row_id = s.id
                ),
                split AS (
                    SELECT reference_id, count(DISTINCT master_hotel_id) AS masters
                    FROM em GROUP BY 1 HAVING count(DISTINCT master_hotel_id) > 1
                )
                SELECT sp.reference_id,
                       sp.masters,
                       -- Structured rather than a joined string: a reviewer
                       -- merging these has to choose which master survives, and
                       -- that needs the provider count and name of each.
                       (SELECT json_agg(json_build_object(
                                   'public_id', reg.public_id,
                                   'master_hotel_id', m.master_hotel_id,
                                   'hotel_name', m.hotel_name,
                                   'city', m.city,
                                   'status', reg.status,
                                   'providers', (SELECT count(*) FROM hotel_mappings h
                                                  WHERE h.master_hotel_id = m.master_hotel_id))
                                 ORDER BY (SELECT count(*) FROM hotel_mappings h
                                            WHERE h.master_hotel_id = m.master_hotel_id) DESC)
                          FROM em e3
                          JOIN master_hotels m ON m.master_hotel_id = e3.master_hotel_id
                          LEFT JOIN master_hotel_registry reg
                                 ON reg.master_hotel_id = m.master_hotel_id
                         WHERE e3.reference_id = sp.reference_id) AS masters_detail,
                       (SELECT string_agg(DISTINCT left(s2.hotel_name, 44), ' | ')
                          FROM reference_mapping r2
                          JOIN supplier_hotels s2
                            ON s2.supplier_name = r2.supplier_name
                           AND s2.supplier_hotel_id = r2.supplier_hotel_id
                         WHERE r2.reference_id = sp.reference_id) AS hotel_names,
                       (SELECT string_agg(DISTINCT coalesce(reg.public_id, '(none)'), ', ')
                          FROM em e2
                          LEFT JOIN master_hotel_registry reg
                                 ON reg.master_hotel_id = e2.master_hotel_id
                         WHERE e2.reference_id = sp.reference_id) AS our_masters,
                       EXISTS (SELECT 1 FROM reference_mapping r3
                               JOIN supplier_hotels s3
                                 ON s3.supplier_name = r3.supplier_name
                                AND s3.supplier_hotel_id = r3.supplier_hotel_id
                               JOIN manual_review_candidates c
                                 ON c.supplier_hotel_id = s3.id
                               WHERE r3.reference_id = sp.reference_id) AS in_review,
                       -- How many supplier records the merge would pull back
                       -- together. This is the page's work-queue ordering: two
                       -- hotels can both be split three ways while one scatters
                       -- nine provider records and the other three, and the
                       -- first is worth a reviewer's attention first.
                       (SELECT count(*)
                          FROM em e4
                          JOIN hotel_mappings h ON h.master_hotel_id = e4.master_hotel_id
                         WHERE e4.reference_id = sp.reference_id) AS provider_records
                FROM split sp
                -- Ordered by fragmentation, then by how much it costs, then by
                -- name so the order is stable and scannable across refreshes.
                -- The previous tiebreak was reference_id — an internal key that
                -- means nothing to the reviewer reading the list, which made a
                -- deliberately ordered page look shuffled.
                ORDER BY sp.masters DESC, provider_records DESC, hotel_names
                LIMIT :limit;
                """
            ),
            {"limit": limit},
        )

        return [dict(r) for r in result.mappings().all()]
