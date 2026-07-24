import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.matching.matcher import (
    CORE_ONLY_NAME_REVIEW_METERS,
    EXACT_NAME_REVIEW_METERS,
)
from app.services.master_identity_service import MasterIdentityService

logger = logging.getLogger(__name__)

# How far out to look for the master a provisional record probably belongs to.
# Wider than the matcher's own radius on purpose: this is a hunting tool shown
# to a person, not a decision, so it should err toward offering too much.
SUGGESTION_RADIUS_METERS = 5000

# Name agreement below this is not worth showing as a suggestion — the reviewer
# would only ever reject it.
MIN_SUGGESTION_NAME_SIMILARITY = 0.35


class ProvisionalMasterService:
    """
    The single-supplier review queue.

    A master seeded by one supplier row is one feed's claim that a property
    exists. Most such claims are true — plenty of hotels are carried by a single
    supplier — but the pipeline cannot tell those apart from the ones that are
    really a hotel it already has, filed under a coordinate or a spelling that
    kept it from matching. So none of them publish until something corroborates
    them.

    Corroboration is automatic when a second supplier attaches. What reaches a
    human is only what is left over, and it is ranked: a provisional master with
    a plausible near neighbour is a probable duplicate and goes first, while one
    with nothing nearby is almost certainly a genuine single-supplier property
    and can be confirmed in bulk.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.identity = MasterIdentityService(session)

    async def summary(self) -> dict:
        result = await self.session.execute(
            text(
                """
                WITH provisional AS (
                    SELECT r.public_id, m.master_hotel_id, m.geo_location,
                           m.core_name, m.strict_name, m.country,
                           m.postal_code, m.address
                    FROM master_hotel_registry r
                    JOIN master_hotels m ON m.master_hotel_id = r.master_hotel_id
                    WHERE r.status = 'Provisional'
                )
                SELECT
                    count(*) AS provisional_masters,
                    count(*) FILTER (WHERE EXISTS (
                        SELECT 1
                        FROM master_hotels o
                        JOIN master_hotel_registry r2
                          ON r2.master_hotel_id = o.master_hotel_id
                         AND r2.status IN ('Active', 'Provisional')
                        WHERE o.master_hotel_id <> provisional.master_hotel_id
                          AND (
                                (lower(o.strict_name) = lower(provisional.strict_name)
                                 AND btrim(coalesce(provisional.strict_name, '')) <> '')
                             OR (provisional.postal_code IS NOT NULL
                                 AND o.postal_code = provisional.postal_code
                                 AND (regexp_match(coalesce(provisional.address, ''), '([0-9]+)'))[1] IS NOT NULL
                                 AND (regexp_match(coalesce(o.address, ''), '([0-9]+)'))[1]
                                     = (regexp_match(coalesce(provisional.address, ''), '([0-9]+)'))[1])
                             OR (o.geo_location IS NOT NULL AND provisional.geo_location IS NOT NULL
                                 AND ST_DWithin(o.geo_location, provisional.geo_location, :radius)
                                 AND lower(o.core_name) = lower(provisional.core_name))
                          )
                    )) AS with_exact_name_neighbour
                FROM provisional;
                """
            ),
            {"radius": SUGGESTION_RADIUS_METERS}
        )

        row = result.mappings().first()

        return dict(row) if row else {
            "provisional_masters": 0,
            "with_exact_name_neighbour": 0,
        }

    async def queue(self, limit: int = 100, offset: int = 0, suspicious_only: bool = False):
        """
        Provisional masters, most suspicious first.

        `suspicion` counts how many other masters nearby share this one's name.
        Anything above zero is a probable duplicate; zero means no evidence of
        one, which is the bulk-confirm case.
        """
        having = "HAVING count(neighbour.master_hotel_id) > 0" if suspicious_only else ""

        result = await self.session.execute(
            text(
                f"""
                SELECT
                    r.public_id,
                    m.master_hotel_id,
                    m.hotel_name,
                    m.address,
                    m.city,
                    m.state,
                    m.country,
                    m.postal_code,
                    m.star_rating,
                    m.latitude,
                    m.longitude,
                    r.first_seen,
                    (SELECT string_agg(DISTINCT hm.supplier_name, ', ')
                       FROM hotel_mappings hm
                      WHERE hm.master_hotel_id = m.master_hotel_id) AS providers,
                    count(neighbour.master_hotel_id) AS suspicion

                FROM master_hotel_registry r
                JOIN master_hotels m ON m.master_hotel_id = r.master_hotel_id

                LEFT JOIN LATERAL (
                    SELECT o.master_hotel_id
                    FROM master_hotels o
                    JOIN master_hotel_registry r2
                      ON r2.master_hotel_id = o.master_hotel_id
                     AND r2.status IN ('Active', 'Provisional')
                    WHERE o.master_hotel_id <> m.master_hotel_id
                      AND (
                            -- an identical full name, or identical postcode +
                            -- building number, is a look-alike at ANY distance —
                            -- the coordinate-error case that a radius hides.
                            (lower(o.strict_name) = lower(m.strict_name)
                             AND btrim(coalesce(m.strict_name, '')) <> '')
                         OR (m.postal_code IS NOT NULL
                             AND o.postal_code = m.postal_code
                             AND (regexp_match(coalesce(m.address, ''), '([0-9]+)'))[1] IS NOT NULL
                             AND (regexp_match(coalesce(o.address, ''), '([0-9]+)'))[1]
                                 = (regexp_match(coalesce(m.address, ''), '([0-9]+)'))[1])
                            -- a bare core name only counts as a look-alike nearby
                         OR (o.geo_location IS NOT NULL AND m.geo_location IS NOT NULL
                             AND ST_DWithin(o.geo_location, m.geo_location, :radius)
                             AND lower(o.core_name) = lower(m.core_name))
                      )
                ) AS neighbour ON TRUE

                WHERE r.status = 'Provisional'
                  AND m.geo_location IS NOT NULL

                GROUP BY r.public_id, m.master_hotel_id, m.hotel_name, m.address,
                         m.city, m.state, m.country, m.postal_code, m.star_rating,
                         m.latitude, m.longitude, r.first_seen
                {having}
                ORDER BY count(neighbour.master_hotel_id) DESC, r.public_id
                LIMIT :limit OFFSET :offset;
                """
            ),
            {"radius": SUGGESTION_RADIUS_METERS, "limit": limit, "offset": offset}
        )

        return [dict(row) for row in result.mappings().all()]

    async def suggestions(self, public_id: str, limit: int = 10):
        """
        The masters this provisional one might really be.

        Distance is a ranking signal, never a filter. A lone master is here
        precisely because distance did not resolve it, and the worst case — a
        supplier coordinate kilometres wrong — is exactly the one a radius filter
        hides: "JP Chennai" and "Jp Hotel" share a postcode and building number
        but sit 11 km apart, so a radius search returned nothing and the reviewer
        saw a lone master with no lead. This searches on name AND postcode +
        building, so every lone master gets its best candidate whatever its GPS
        says.
        """
        resolved = await self.identity.resolve(public_id)

        if resolved is None:
            return {"ok": False, "error": "Unknown public id"}

        result = await self.session.execute(
            text(
                """
                WITH target AS (
                    SELECT m.master_hotel_id, m.geo_location, m.core_name,
                           m.strict_name, m.hotel_name, m.country, m.postal_code,
                           (regexp_match(coalesce(m.address, ''), '([0-9]+)'))[1]
                               AS building_num
                    FROM master_hotel_registry r
                    JOIN master_hotels m ON m.master_hotel_id = r.master_hotel_id
                    WHERE r.public_id = :public_id
                )
                SELECT
                    r.public_id,
                    r.status,
                    o.master_hotel_id,
                    o.hotel_name,
                    o.address,
                    o.city,
                    o.postal_code,
                    o.star_rating,
                    o.latitude,
                    o.longitude,
                    CASE WHEN o.geo_location IS NOT NULL AND t.geo_location IS NOT NULL
                         THEN round(ST_Distance(o.geo_location, t.geo_location)::numeric, 0)
                    END AS distance_meters,
                    round((similarity(lower(o.hotel_name),
                                      lower(t.hotel_name)) * 100)::numeric, 0)
                        AS name_match_pct,
                    (lower(o.strict_name) = lower(t.strict_name)) AS strict_name_match,
                    (lower(o.core_name)   = lower(t.core_name))   AS core_name_match,
                    (o.postal_code IS NOT NULL
                     AND o.postal_code = t.postal_code
                     AND t.building_num IS NOT NULL
                     AND (regexp_match(coalesce(o.address, ''), '([0-9]+)'))[1]
                         = t.building_num) AS address_match,
                    (SELECT string_agg(DISTINCT hm.supplier_name, ', ')
                       FROM hotel_mappings hm
                      WHERE hm.master_hotel_id = o.master_hotel_id) AS providers

                FROM target t
                JOIN master_hotels o
                  ON o.master_hotel_id <> t.master_hotel_id
                 AND lower(o.country) = lower(t.country)
                JOIN master_hotel_registry r
                  ON r.master_hotel_id = o.master_hotel_id
                 AND r.status IN ('Active', 'Provisional')

                -- Strong signals are trusted at any distance, because they are
                -- what a bad coordinate cannot fake: a fully identical name, or
                -- an identical postcode + building number. The weaker signals —
                -- a bare core name like "jp" or a fuzzy name match — are only
                -- meaningful nearby, or "jp" would pull every JP hotel in the
                -- country, so they keep a distance gate.
                WHERE lower(o.strict_name) = lower(t.strict_name)
                   OR (o.postal_code IS NOT NULL
                       AND o.postal_code = t.postal_code
                       AND t.building_num IS NOT NULL
                       AND (regexp_match(coalesce(o.address, ''), '([0-9]+)'))[1]
                           = t.building_num)
                   OR (
                        o.geo_location IS NOT NULL AND t.geo_location IS NOT NULL
                        AND ST_DWithin(o.geo_location, t.geo_location, :radius)
                        AND (
                            lower(o.core_name) = lower(t.core_name)
                            OR similarity(lower(o.hotel_name),
                                          lower(t.hotel_name)) > :min_similarity
                        )
                   )

                ORDER BY (lower(o.strict_name) = lower(t.strict_name)) DESC,
                         (o.postal_code = t.postal_code
                          AND (regexp_match(coalesce(o.address,''),'([0-9]+)'))[1]
                              = t.building_num) DESC,
                         (lower(o.core_name)   = lower(t.core_name))   DESC,
                         similarity(lower(o.hotel_name), lower(t.hotel_name)) DESC
                LIMIT :limit;
                """
            ),
            {
                "public_id": resolved,
                "radius": SUGGESTION_RADIUS_METERS,
                "min_similarity": MIN_SUGGESTION_NAME_SIMILARITY,
                "limit": limit,
            }
        )

        rows = [dict(row) for row in result.mappings().all()]

        return {"ok": True, "public_id": resolved, "suggestions": rows,
                "count": len(rows)}

    async def fragmented_masters(self, limit: int = 200):
        """
        Masters that are probably one hotel filed twice.

        The retrospective half of the exact-name change: new rules only govern
        records processed from now on, and the duplicates already in the table
        stay split until someone looks at them. Same name, close enough that two
        distinct properties is unlikely, sitting under different ids.

        The radius depends on how the names agree, matching the live matcher —
        an agreement that survives the city being in the name is worth trusting
        further out than one that only appears after stripping it, which can
        leave nothing but a chain name.
        """
        result = await self.session.execute(
            text(
                """
                SELECT
                    ra.public_id        AS master_id_a,
                    rb.public_id        AS master_id_b,
                    a.hotel_name        AS hotel_name_a,
                    b.hotel_name        AS hotel_name_b,
                    a.address           AS address_a,
                    b.address           AS address_b,
                    a.city              AS city_a,
                    b.city              AS city_b,
                    a.postal_code       AS postal_code_a,
                    b.postal_code       AS postal_code_b,
                    ra.status           AS status_a,
                    rb.status           AS status_b,
                    round(ST_Distance(a.geo_location, b.geo_location)::numeric, 0)
                        AS distance_meters,
                    CASE
                        WHEN lower(a.strict_name) = lower(b.strict_name)
                             AND ST_DWithin(a.geo_location, b.geo_location, :strict_radius)
                             THEN 'STRICT'
                        WHEN lower(a.core_name) = lower(b.core_name)
                             AND ST_DWithin(a.geo_location, b.geo_location, :core_radius)
                             THEN 'CORE_ONLY'
                        ELSE 'SAME_ADDRESS'
                    END AS match_class,
                    (a.postal_code IS NOT DISTINCT FROM b.postal_code) AS postal_agrees,
                    (SELECT count(DISTINCT hm.supplier_name) FROM hotel_mappings hm
                      WHERE hm.master_hotel_id = a.master_hotel_id) AS providers_a,
                    (SELECT count(DISTINCT hm.supplier_name) FROM hotel_mappings hm
                      WHERE hm.master_hotel_id = b.master_hotel_id) AS providers_b

                FROM master_hotels a
                JOIN master_hotels b
                  ON b.master_hotel_id > a.master_hotel_id
                 AND lower(a.country) = lower(b.country)
                 AND a.geo_location IS NOT NULL
                 AND b.geo_location IS NOT NULL
                 AND (
                       (lower(a.strict_name) = lower(b.strict_name)
                        AND btrim(coalesce(a.strict_name, '')) <> ''
                        AND ST_DWithin(a.geo_location, b.geo_location, :strict_radius))
                    OR (lower(a.core_name) = lower(b.core_name)
                        AND btrim(coalesce(a.core_name, '')) <> ''
                        AND ST_DWithin(a.geo_location, b.geo_location, :core_radius))
                    -- Coordinate-independent arm: same postcode AND same building
                    -- number catch a duplicate whose GPS is wrong, which distance
                    -- never can — "JP Chennai" and "Jp Hotel", both 1131 / 600107,
                    -- sat 11 km apart because one supplier's latitude was garbage.
                    -- The name guard keeps two genuinely different businesses at
                    -- one address (a mall, a repeated plot number) from pairing.
                    OR (
                        a.postal_code IS NOT NULL
                        AND a.postal_code = b.postal_code
                        -- a postcode is a locality; two masters sharing one that
                        -- sit 280 km apart are a data error, not a duplicate
                        AND ST_DWithin(a.geo_location, b.geo_location, 50000)
                        AND (regexp_match(coalesce(a.address, ''), '([0-9]+)'))[1] IS NOT NULL
                        AND (regexp_match(coalesce(a.address, ''), '([0-9]+)'))[1]
                            = (regexp_match(coalesce(b.address, ''), '([0-9]+)'))[1]
                        AND (
                            (lower(a.core_name) = lower(b.core_name)
                             AND btrim(coalesce(a.core_name, '')) <> '')
                            OR similarity(lower(a.hotel_name), lower(b.hotel_name)) > 0.3
                        )
                    )
                    -- Same postcode + identical full name, no building number —
                    -- "JW Marriott Mumbai Juhu" on "Juhu Tara Road" (no number),
                    -- same postcode 400049, GPS 5 km apart.
                    OR (
                        a.postal_code IS NOT NULL
                        AND a.postal_code = b.postal_code
                        AND ST_DWithin(a.geo_location, b.geo_location, 50000)
                        AND lower(a.strict_name) = lower(b.strict_name)
                        AND btrim(coalesce(a.strict_name, '')) <> ''
                    )
                 )
                 -- Conflicting numbers in the two names mean different
                 -- properties, not one duplicated — "Leisure Valley 1" and
                 -- "Leisure Valley 2" reduce to the same core name only because
                 -- single-digit tokens are dropped. Exclude them so the sweep
                 -- does not send a reviewer a pair the data already separates.
                 AND NOT (
                     (SELECT array_agg(DISTINCT x) FROM regexp_matches(lower(a.hotel_name), '\\y\\d+\\y', 'g') AS t(x)) IS DISTINCT FROM
                     (SELECT array_agg(DISTINCT x) FROM regexp_matches(lower(b.hotel_name), '\\y\\d+\\y', 'g') AS t(x))
                     AND (SELECT array_agg(DISTINCT x) FROM regexp_matches(lower(a.hotel_name), '\\y\\d+\\y', 'g') AS t(x)) IS NOT NULL
                     AND (SELECT array_agg(DISTINCT x) FROM regexp_matches(lower(b.hotel_name), '\\y\\d+\\y', 'g') AS t(x)) IS NOT NULL
                 )

                JOIN master_hotel_registry ra
                  ON ra.master_hotel_id = a.master_hotel_id
                 AND ra.status IN ('Active', 'Provisional')
                JOIN master_hotel_registry rb
                  ON rb.master_hotel_id = b.master_hotel_id
                 AND rb.status IN ('Active', 'Provisional')

                -- A reviewer already ruled on this pair.
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM master_non_merge_assertion nm
                    WHERE EXISTS (
                            SELECT 1 FROM hotel_mappings ha
                            WHERE ha.master_hotel_id = a.master_hotel_id
                              AND ha.supplier_hotel_row_id IN (nm.row_id_a, nm.row_id_b)
                          )
                      AND EXISTS (
                            SELECT 1 FROM hotel_mappings hb
                            WHERE hb.master_hotel_id = b.master_hotel_id
                              AND hb.supplier_hotel_row_id IN (nm.row_id_a, nm.row_id_b)
                          )
                )

                ORDER BY
                    CASE WHEN lower(a.strict_name) = lower(b.strict_name)
                         THEN 0 ELSE 1 END,
                    ST_Distance(a.geo_location, b.geo_location) ASC
                LIMIT :limit;
                """
            ),
            {
                "strict_radius": EXACT_NAME_REVIEW_METERS,
                "core_radius": CORE_ONLY_NAME_REVIEW_METERS,
                "limit": limit,
            }
        )

        rows = [dict(row) for row in result.mappings().all()]

        return {"pairs": rows, "count": len(rows)}
