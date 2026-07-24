import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# A correct master holds one record per supplier, all describing one building.
# Five suppliers legitimately produce five spellings of the name, so counting
# distinct name STRINGS flags healthy masters. The signals that actually
# indicate absorption are geographic spread and weak name agreement — the shape
# of the failure that merged ~11 properties into one "Hilton Mumbai Airport".
MAX_SPREAD_METERS = 400
MIN_WORST_NAME_SIMILARITY = 60


class IntegrityMonitor:
    """
    Post-run safety net.

    Accuracy cannot be assured by thresholds alone: at 10 lakh records nobody
    verifies mappings one by one, so the system has to detect its own failures.
    These checks look for the *shape* of a bad merge rather than for any
    particular pair, and they run after every mapping batch.

    Nothing here mutates mappings. It reports, so a human decides.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def absorbing_masters(self, limit: int = 100):
        """
        Masters holding too many distinct hotel names, or whose members are
        spread too far apart to be one building.
        """
        result = await self.session.execute(
            text(
                """
                SELECT
                    m.master_hotel_id,
                    m.hotel_name,
                    m.city,
                    count(*) AS mapping_count,
                    count(DISTINCT lower(s.hotel_name)) AS distinct_names,
                    count(DISTINCT s.supplier_name) AS supplier_count,
                    round(max(
                        ST_Distance(s.geo_location, m.geo_location)
                    )::numeric, 1) AS max_spread_meters,
                    round(min(hm.name_similarity), 1) AS worst_name_similarity
                FROM hotel_mappings hm
                JOIN master_hotels m
                  ON m.master_hotel_id = hm.master_hotel_id
                JOIN supplier_hotels s
                  ON s.id = hm.supplier_hotel_row_id
                GROUP BY m.master_hotel_id, m.hotel_name, m.city
                HAVING max(ST_Distance(s.geo_location, m.geo_location)) > :max_spread
                    OR min(hm.name_similarity) < :min_name_sim
                ORDER BY max(ST_Distance(s.geo_location, m.geo_location)) DESC
                LIMIT :limit;
                """
            ),
            {
                "max_spread": MAX_SPREAD_METERS,
                "min_name_sim": MIN_WORST_NAME_SIMILARITY,
                "limit": limit,
            }
        )

        return [dict(row) for row in result.mappings().all()]

    async def same_supplier_collisions(self, limit: int = 100):
        """
        One master holding two hotels from the SAME supplier is a contradiction:
        a supplier does not list one property twice under different names, so the
        two are almost certainly different hotels wrongly merged.

        This is the single highest-precision false-positive signal available and
        needs no ground truth to compute.
        """
        result = await self.session.execute(
            text(
                """
                SELECT
                    hm.master_hotel_id,
                    m.hotel_name AS master_hotel_name,
                    hm.supplier_name,
                    count(*) AS records_from_same_supplier,
                    string_agg(DISTINCT left(s.hotel_name, 40), ' | ') AS names
                FROM hotel_mappings hm
                JOIN master_hotels m
                  ON m.master_hotel_id = hm.master_hotel_id
                JOIN supplier_hotels s
                  ON s.id = hm.supplier_hotel_row_id
                GROUP BY hm.master_hotel_id, m.hotel_name, hm.supplier_name
                HAVING count(DISTINCT lower(s.hotel_name)) > 1
                ORDER BY count(*) DESC
                LIMIT :limit;
                """
            ),
            {"limit": limit}
        )

        return [dict(row) for row in result.mappings().all()]

    async def confidence_breakdown(self):
        result = await self.session.execute(
            text(
                """
                SELECT
                    coalesce(confidence_tier, 'UNKNOWN') AS confidence_tier,
                    count(*) AS mappings
                FROM hotel_mappings
                WHERE mapping_type = 'AUTO'
                GROUP BY 1
                ORDER BY 1;
                """
            )
        )

        return [dict(row) for row in result.mappings().all()]

    async def run_all(self):
        absorbing = await self.absorbing_masters()
        collisions = await self.same_supplier_collisions()
        confidence = await self.confidence_breakdown()

        report = {
            "absorbing_masters": absorbing,
            "same_supplier_collisions": collisions,
            "confidence_breakdown": confidence,
            "absorbing_master_count": len(absorbing),
            "same_supplier_collision_count": len(collisions),
        }

        if collisions:
            logger.warning(
                "Integrity: %d masters hold multiple hotels from the same "
                "supplier — probable false merges",
                len(collisions)
            )

        if absorbing:
            logger.warning(
                "Integrity: %d masters look like they are absorbing neighbours",
                len(absorbing)
            )

        return report
