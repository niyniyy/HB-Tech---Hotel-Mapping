import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class SearchService:
    """
    Multi-field search for master hotels and the manual review queue.

    Every filter is optional and they combine with AND. A search runs as soon as
    any one field has a value, so the caller never has to fill in a form before
    seeing results.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    # ── facets for the dropdowns ────────────────────────────────────────────

    async def facets(self):
        suppliers = await self.session.execute(
            text("SELECT DISTINCT supplier_name FROM supplier_hotels ORDER BY 1;")
        )
        countries = await self.session.execute(
            text(
                """
                SELECT country, count(*) AS n
                FROM master_hotels
                WHERE country IS NOT NULL AND btrim(country) <> ''
                GROUP BY 1 ORDER BY 2 DESC LIMIT 300;
                """
            )
        )
        chains = await self.session.execute(
            text(
                """
                SELECT DISTINCT chain_name FROM master_hotels
                WHERE chain_name IS NOT NULL AND btrim(chain_name) <> ''
                ORDER BY 1 LIMIT 500;
                """
            )
        )
        types = await self.session.execute(
            text(
                """
                SELECT DISTINCT property_type FROM master_hotels
                WHERE property_type IS NOT NULL AND btrim(property_type) <> ''
                ORDER BY 1 LIMIT 200;
                """
            )
        )

        chain_values = [r[0] for r in chains.fetchall()]
        type_values = [r[0] for r in types.fetchall()]

        # Star is the dangerous filter on this data: only 18% of supplier
        # records carry a rating, and `star_rating >= n` excludes NULLs, so
        # nudging the slider off zero silently discards four records in five.
        # Chain and property type already announce that they are empty; star
        # looks usable and is mostly not, which is worse.
        star = await self.session.execute(
            text(
                """
                SELECT count(*) AS total, count(star_rating) AS rated
                FROM supplier_hotels;
                """
            )
        )
        star_row = star.mappings().first()
        rated = star_row["rated"] or 0
        total = star_row["total"] or 0

        return {
            "providers": [r[0] for r in suppliers.fetchall()],
            "countries": [dict(r) for r in countries.mappings().all()],
            "chains": chain_values,
            "property_types": type_values,
            # Tell the UI which filters currently have nothing behind them, so a
            # user is not left wondering why a search returns no rows.
            "empty_fields": [
                name for name, values in
                (("chain_name", chain_values), ("property_type", type_values))
                if not values
            ],
            "star_coverage": {
                "rated": rated,
                "total": total,
                "pct": round(100.0 * rated / total, 1) if total else 0.0,
            },
        }

    # ── record search ───────────────────────────────────────────────────────

    @staticmethod
    def _filters(params: dict):
        """
        Build only the WHERE fragments actually needed, so unused filters cost
        nothing in the plan.
        """
        clauses = []

        if params.get("q"):
            clauses.append(
                "(s.hotel_name ILIKE :q_like OR m.hotel_name ILIKE :q_like "
                " OR s.address ILIKE :q_like)"
            )
        if params.get("master_id"):
            clauses.append("r.public_id ILIKE :master_like")
        if params.get("provider_hotel_id"):
            clauses.append("s.supplier_hotel_id ILIKE :provider_hotel_like")
        if params.get("provider_name"):
            clauses.append("s.supplier_name = :provider_name")
        if params.get("chain_name"):
            clauses.append(
                "(m.chain_name ILIKE :chain_like OR s.chain_name ILIKE :chain_like)"
            )
        if params.get("property_type"):
            clauses.append(
                "(m.property_type ILIKE :ptype_like OR s.property_type ILIKE :ptype_like)"
            )
        if params.get("country"):
            clauses.append("s.country ILIKE :country_like")
        if params.get("city"):
            clauses.append("s.city ILIKE :city_like")
        if params.get("star_min") is not None:
            clauses.append("s.star_rating >= :star_min")

        return clauses

    @staticmethod
    def _bind(params: dict):
        bound = {}
        mapping = {
            "q": ("q_like", "%{}%"),
            "master_id": ("master_like", "%{}%"),
            "provider_hotel_id": ("provider_hotel_like", "%{}%"),
            "provider_name": ("provider_name", "{}"),
            "chain_name": ("chain_like", "%{}%"),
            "property_type": ("ptype_like", "%{}%"),
            "country": ("country_like", "%{}%"),
            "city": ("city_like", "%{}%"),
        }

        for field, (key, pattern) in mapping.items():
            if params.get(field):
                bound[key] = pattern.format(params[field])

        if params.get("star_min") is not None:
            bound["star_min"] = params["star_min"]

        return bound

    async def search_records(self, params: dict, limit: int = 50, offset: int = 0):
        """
        Return one row per master hotel — the property, not the provider records.

        The filters still run against the supplier rows, so searching a provider
        hotel id finds the master that record belongs to; the result is that
        master, once, under its own canonical name. The provider records behind
        it are the drill-down (`master_detail`), which is where a reviewer judges
        whether they really are the same hotel.

        Paging therefore counts properties, which is what "showing 1-50" should
        mean on a screen called Master Hotels.
        """
        clauses = self._filters(params)

        if not clauses:
            return {"results": [], "count": 0, "searched": False,
                    "limit": limit, "offset": offset, "has_more": False}

        where = " AND ".join(clauses)
        bound = self._bind(params)
        bound.update({"limit": limit + 1, "offset": offset})

        result = await self.session.execute(
            text(
                f"""
                WITH matched AS (
                    SELECT hm.master_hotel_id
                    FROM hotel_mappings hm
                    JOIN supplier_hotels s ON s.id = hm.supplier_hotel_row_id
                    JOIN master_hotels m ON m.master_hotel_id = hm.master_hotel_id
                    LEFT JOIN master_hotel_registry r
                           ON r.master_hotel_id = hm.master_hotel_id
                    WHERE {where}
                    GROUP BY hm.master_hotel_id
                )
                SELECT r.public_id           AS master_id,
                       -- Provisional masters stay visible to reviewers (they
                       -- are the queue) but are labelled, so nobody mistakes an
                       -- uncorroborated single-supplier claim for a published
                       -- property. Exports drop them entirely.
                       r.status              AS master_status,
                       m.master_hotel_id,
                       m.hotel_name,
                       m.address, m.city, m.state, m.country, m.postal_code,
                       m.star_rating, m.latitude, m.longitude,
                       m.chain_name, m.property_type,
                       -- every provider on the master, not just the ones that
                       -- matched the filter: the row represents the property.
                       count(hm.id)                        AS provider_count,
                       string_agg(DISTINCT hm.supplier_name, ', ') AS providers,
                       min(hm.supplier_hotel_row_id)       AS supplier_hotel_row_id
                FROM matched mt
                JOIN master_hotels m ON m.master_hotel_id = mt.master_hotel_id
                JOIN hotel_mappings hm ON hm.master_hotel_id = mt.master_hotel_id
                LEFT JOIN master_hotel_registry r
                       ON r.master_hotel_id = mt.master_hotel_id
                GROUP BY r.public_id, r.status, m.master_hotel_id, m.hotel_name, m.address,
                         m.city, m.state, m.country, m.postal_code, m.star_rating,
                         m.latitude, m.longitude, m.chain_name, m.property_type
                ORDER BY r.public_id
                LIMIT :limit OFFSET :offset;
                """
            ),
            bound,
        )

        rows = [dict(row) for row in result.mappings().all()]
        has_more = len(rows) > limit

        return {
            "results": rows[:limit],
            "count": len(rows[:limit]),
            "searched": True,
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
        }

    async def count_records(self, params: dict):
        """Backs 'Get Property Count' — totals across the whole match, not a page."""
        clauses = self._filters(params)
        where = " AND ".join(clauses) if clauses else "TRUE"

        result = await self.session.execute(
            text(
                f"""
                SELECT count(DISTINCT m.master_hotel_id) AS properties,
                       count(*)                          AS supplier_records,
                       count(DISTINCT s.supplier_name)   AS providers
                FROM hotel_mappings hm
                JOIN supplier_hotels s ON s.id = hm.supplier_hotel_row_id
                JOIN master_hotels m ON m.master_hotel_id = hm.master_hotel_id
                LEFT JOIN master_hotel_registry r
                       ON r.master_hotel_id = hm.master_hotel_id
                WHERE {where};
                """
            ),
            self._bind(params),
        )

        return dict(result.mappings().first())

    async def find_duplicates(self, supplier_row_id: int, radius_m: int = 1000):
        """
        Hotels near this record that may be the same property but sit under a
        different master. Deliberately loose — this is a hunting tool, so it
        errs toward showing too much rather than too little.
        """
        result = await self.session.execute(
            text(
                """
                WITH target AS (
                    SELECT s.id, s.hotel_name, s.geo_location, hm.master_hotel_id
                    FROM supplier_hotels s
                    LEFT JOIN hotel_mappings hm ON hm.supplier_hotel_row_id = s.id
                    WHERE s.id = :row_id
                )
                SELECT r.public_id         AS master_id,
                       o.id                AS supplier_hotel_row_id,
                       o.supplier_name     AS provider_name,
                       o.supplier_hotel_id AS provider_hotel_id,
                       o.hotel_name, o.address, o.city, o.star_rating,
                       o.latitude, o.longitude,
                       round(ST_Distance(o.geo_location, t.geo_location)::numeric, 0)
                           AS distance_meters,
                       round((similarity(lower(o.hotel_name), lower(t.hotel_name))
                              * 100)::numeric, 0) AS name_match_pct
                FROM target t
                JOIN supplier_hotels o
                  ON o.id <> t.id
                 AND o.geo_location IS NOT NULL
                 AND ST_DWithin(o.geo_location, t.geo_location, :radius)
                LEFT JOIN hotel_mappings hm2 ON hm2.supplier_hotel_row_id = o.id
                LEFT JOIN master_hotel_registry r
                       ON r.master_hotel_id = hm2.master_hotel_id
                WHERE hm2.master_hotel_id IS DISTINCT FROM t.master_hotel_id
                  AND similarity(lower(o.hotel_name), lower(t.hotel_name)) > 0.25
                ORDER BY similarity(lower(o.hotel_name), lower(t.hotel_name)) DESC
                LIMIT 25;
                """
            ),
            {"row_id": supplier_row_id, "radius": radius_m},
        )

        rows = [dict(r) for r in result.mappings().all()]

        return {"candidates": rows, "count": len(rows)}

    # ── manual review search ────────────────────────────────────────────────

    async def search_manual_review(self, q: str = None, limit: int = 200):
        """
        Search the review queue across every field at once — supplier, hotel
        name, id, city, country, the suggested master, or the reason.
        """
        clause = ""
        bound = {"limit": limit}

        if q:
            clause = """
                AND (s.hotel_name ILIKE :like OR s.supplier_hotel_id ILIKE :like
                     OR s.supplier_name ILIKE :like OR s.city ILIKE :like
                     OR s.state ILIKE :like OR s.country ILIKE :like
                     OR s.address ILIKE :like OR s.postal_code ILIKE :like
                     OR m.hotel_name ILIKE :like OR r.public_id ILIKE :like
                     OR c.decision_reason ILIKE :like)
            """
            bound["like"] = f"%{q}%"

        result = await self.session.execute(
            text(
                f"""
                SELECT c.supplier_hotel_id AS supplier_hotel_row_id,
                       s.supplier_name, s.supplier_hotel_id, s.hotel_name,
                       s.address, s.city, s.state, s.country, s.postal_code,
                       s.latitude, s.longitude, s.star_rating,
                       m.hotel_name AS master_hotel_name,
                       r.public_id AS master_public_id,
                       m.city AS master_city, m.address AS master_address,
                       c.rule_score, c.ai_similarity, c.decision_reason,
                       c.review_type, c.distance_meters, c.created_at
                FROM manual_review_candidates c
                JOIN supplier_hotels s ON s.id = c.supplier_hotel_id
                JOIN master_hotels m ON m.master_hotel_id = c.suggested_master_hotel_id
                LEFT JOIN master_hotel_registry r
                       ON r.master_hotel_id = c.suggested_master_hotel_id
                WHERE TRUE {clause}
                -- The two escalation kinds carry different evidence: an
                -- exact-name conflict has a rule score and no AI score, a
                -- semantic duplicate the reverse. Ordering on rule_score alone
                -- therefore sorted every AI finding to the very bottom of the
                -- queue, behind 651 rows, where nobody scrolled to it. Both
                -- numbers are 0-100 confidence in the same sense — how sure the
                -- system is that these are one hotel — so rank on whichever the
                -- row actually has.
                ORDER BY coalesce(c.rule_score, c.ai_similarity) DESC NULLS LAST
                LIMIT :limit;
                """
            ),
            bound,
        )

        rows = [dict(r) for r in result.mappings().all()]

        return {"results": rows, "count": len(rows)}
