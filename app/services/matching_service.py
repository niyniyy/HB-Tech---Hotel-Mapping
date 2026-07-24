import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.matching.matcher import (
    CORE_ONLY_NAME_REVIEW_METERS,
    EXACT_NAME_REVIEW_METERS,
    calculate_rule_based_score,
)
from app.normalization.normalizer import core_hotel_name

# Rough bounding box for India. A coordinate outside this cannot be trusted for
# a domestic supplier feed and must not be used to seed or match a master.
COUNTRY_BOUNDS = {
    "india": (6.0, 37.0, 68.0, 98.0),
}

# When two rows share a supplier id, do they describe one hotel or two?
#
# Distance buys tolerance for a weaker name, exactly as the matcher's tiers do.
# "Clarks Inn Gurgaon" and "DS Clarks Inn Gurgaon" are one hotel 3 m apart whose
# core names agree only 0.39 — the brand prefix fails a name test that is right
# for records a hundred metres apart; below 50 m, co-location is the stronger
# evidence. Anything with an identical name is one hotel whatever the distance:
# a re-audit against the reference found 42 groups sharing an id and a name but
# held apart only by coordinate scatter — Azaya Beach Resort with one pin 28 km
# off, Walisons Hotel at 4.5 km — every one a single property with a bad
# coordinate, none a genuine collision. The matcher already trusts an exact name
# out to 5 km for the same reason; the discard classifier was stricter than the
# thing it feeds, and silently threw those hotels away rather than keeping one
# row. A true collision is distinguished by a DIFFERENT name (Aiden by Best
# Western vs Golden Tulip under one id), never by distance alone.
#
# (max_metres, min_name_similarity) — None distance means "any distance".
DUPLICATE_ROW_TIERS = [
    (None, 0.90),
    (200.0, 0.60),
    (50.0, 0.30),
]


def check_record_completeness(supplier_hotel):
    """
    Return a flag reason if the record is not fit for automatic processing,
    otherwise None. Flagged records are never mapped and never become masters —
    they are reported back so the supplier can correct them.
    """
    latitude = supplier_hotel.get("latitude")
    longitude = supplier_hotel.get("longitude")

    if latitude is None or longitude is None:
        return "MISSING_COORDINATES"

    latitude = float(latitude)
    longitude = float(longitude)

    if latitude == 0 and longitude == 0:
        return "INVALID_COORDINATES"

    country = (supplier_hotel.get("country") or "").strip()
    city = (supplier_hotel.get("city") or "").strip()
    hotel_name = (supplier_hotel.get("hotel_name") or "").strip()

    if not hotel_name:
        return "MISSING_NAME"

    if not city:
        return "MISSING_CITY"

    if not country:
        return "MISSING_COUNTRY"

    bounds = COUNTRY_BOUNDS.get(country.lower())

    if bounds is not None:
        min_lat, max_lat, min_lon, max_lon = bounds

        if not (min_lat <= latitude <= max_lat and min_lon <= longitude <= max_lon):
            return "COORDINATES_OUTSIDE_COUNTRY"

    if not core_hotel_name(hotel_name, city):
        return "NAME_NOT_DISTINGUISHING"

    return None


class MatchingService:
    """
    Async matching service.

    This service:
    1. Fetches one supplier hotel
    2. Finds candidate master hotels
    3. Applies rule-based scoring
    4. Selects the best candidate
    5. Returns candidate object with rule_score and rule_decision
    """

    def __init__(self, session: AsyncSession):
        self.session = session

        # Corpus document frequency for the tokens of the record being scored.
        # Loaded once per supplier record and reused across its 25 candidates:
        # the statistic is a property of the corpus, not of the pair, so looking
        # it up per candidate would be 25 queries to learn the same thing.
        self._token_df: dict[str, int] = {}

        # Rarity band edges, percentiles of the whole corpus. Loaded once and
        # cached for the life of the service — they change only when the token
        # table is rebuilt, which happens between runs, not during one. None
        # means "not loaded yet"; the scorer then falls back to its defaults.
        self._rarity_cutoffs: tuple[int, int, int] | None = None

    @staticmethod
    def _name_tokens(name) -> set[str]:
        """Words worth counting — the same shape the df table is built from."""
        if not name:
            return set()

        cleaned = re.sub(r"[^a-z0-9 ]", " ", str(name).lower())

        return {token for token in cleaned.split() if len(token) > 2}

    async def rarity_cutoffs(self) -> tuple[int, int, int] | None:
        """
        The corpus's rarity band edges, loaded once. None if never computed —
        the scorer then uses its own defaults rather than misgrading.
        """
        if self._rarity_cutoffs is not None:
            return self._rarity_cutoffs

        result = await self.session.execute(
            text("SELECT band, cutoff_df FROM name_rarity_cutoffs;")
        )
        edges = {row[0]: row[1] for row in result.fetchall()}

        if {"full", "strong", "weak"} <= edges.keys():
            self._rarity_cutoffs = (edges["full"], edges["strong"], edges["weak"])

        return self._rarity_cutoffs

    async def load_token_frequencies(self, hotel_name) -> None:
        """
        Fetch how common each word of this hotel's name is across the corpus.

        Only the words in this one name are needed, so this stays a small
        indexed lookup rather than anything that grows with the corpus.
        """
        tokens = self._name_tokens(hotel_name)

        if not tokens:
            self._token_df = {}
            return

        result = await self.session.execute(
            text(
                """
                SELECT token, df FROM name_token_df
                WHERE token = ANY(:tokens);
                """
            ),
            {"tokens": list(tokens)},
        )

        self._token_df = {row[0]: row[1] for row in result.fetchall()}

    def rarest_shared_token_df(self, master_hotel_name, supplier_hotel_name):
        """
        Document frequency of the least common word the two names share.

        None when they share nothing distinctive — which is itself the strongest
        form of the signal, since it is true of 42% of different-hotel pairs and
        almost no same-hotel ones.
        """
        shared = self._name_tokens(master_hotel_name) & self._name_tokens(
            supplier_hotel_name
        )

        frequencies = [
            self._token_df[token] for token in shared if token in self._token_df
        ]

        return min(frequencies) if frequencies else None

    async def get_supplier_hotel(self, supplier_hotel_record_id: int):
        result = await self.session.execute(
            text(
                """
                SELECT *
                FROM supplier_hotels
                WHERE id = :supplier_hotel_record_id;
                """
            ),
            {"supplier_hotel_record_id": supplier_hotel_record_id}
        )

        return result.mappings().first()

    async def find_candidate_master_hotels(self, supplier_hotel_record_id: int):
        """
        Geo-first candidate search.

        The city is deliberately NOT part of the join. Suppliers disagree on city
        names — Bangalore/Bengaluru, Gurgaon/Gurugram, New Delhi/New Delhi And NCR
        — and requiring an exact match meant the same hotel filed under two
        spellings could never become a candidate, which fragmented the master
        table. Country plus a 1 km radius is a sufficient filter; the city is
        still used when comparing names, to strip it out of them.

        Supplier coordinate is COALESCE(s.match_geo_location, s.geo_location).
        The geocode correction (scripts/geocode_correct.py) writes
        match_geo_location for records whose supplied coordinate is a suspected
        error, so such a record is searched from where the hotel actually is
        rather than fragmenting into its own master. match_geo_location is NULL
        until a correction is written, so this is the original coordinate by
        default — the same COALESCE is used in the name-first search below.
        """
        result = await self.session.execute(
            text(
                """
                SELECT
                    s.id AS supplier_hotel_record_id,
                    s.supplier_name,
                    s.supplier_hotel_id,
                    s.hotel_name AS supplier_hotel_name,
                    s.normalized_name AS supplier_normalized_name,
                    s.address AS supplier_normalized_address,
                    s.star_rating AS supplier_star_rating,
                    s.city AS supplier_city,
                    s.state AS supplier_state,
                    s.postal_code AS supplier_postal_code,

                    m.master_hotel_id,
                    m.hotel_name AS master_hotel_name,
                    m.normalized_name AS master_normalized_name,
                    m.address AS master_normalized_address,
                    m.star_rating AS master_star_rating,
                    m.city AS master_city,
                    m.state AS master_state,
                    m.postal_code AS master_postal_code,

                    s.city,
                    s.country,

                    ST_Distance(m.geo_location, COALESCE(s.match_geo_location, s.geo_location)) AS distance_meters

                FROM supplier_hotels s
                JOIN master_hotels m
                  ON LOWER(m.country) = LOWER(s.country)

                WHERE s.id = :supplier_hotel_record_id
                  AND COALESCE(s.match_geo_location, s.geo_location) IS NOT NULL
                  AND m.geo_location IS NOT NULL
                  AND ST_DWithin(m.geo_location, COALESCE(s.match_geo_location, s.geo_location), 1000)

                  -- Structural invariant: one supplier contributes at most one
                  -- hotel to a master. A supplier does not list the same
                  -- property twice under different names, so if this master
                  -- already holds a different hotel from this supplier, the two
                  -- are different properties and this is a false merge.
                  --
                  -- Enforced here rather than merely detected afterwards. The
                  -- cost is that a genuine duplicate listing within one supplier
                  -- feed becomes a second master instead of merging — a
                  -- duplicate, which is recoverable, in place of a false merge,
                  -- which is not.
                  AND NOT EXISTS (
                      SELECT 1
                      FROM hotel_mappings hm
                      WHERE hm.master_hotel_id = m.master_hotel_id
                        AND hm.supplier_name = s.supplier_name
                        AND hm.supplier_hotel_id <> s.supplier_hotel_id
                  )

                  -- A reviewer has stated these are different hotels. That
                  -- decision outranks the algorithm permanently: without this
                  -- clause the next run simply re-merges what was just split,
                  -- and reviewing becomes pointless work.
                  AND NOT EXISTS (
                      SELECT 1
                      FROM master_non_merge_assertion nm
                      JOIN hotel_mappings hm2
                        ON hm2.master_hotel_id = m.master_hotel_id
                      WHERE (nm.row_id_a = s.id AND nm.row_id_b = hm2.supplier_hotel_row_id)
                         OR (nm.row_id_b = s.id AND nm.row_id_a = hm2.supplier_hotel_row_id)
                  )

                ORDER BY distance_meters ASC
                LIMIT 25;
                """
            ),
            {"supplier_hotel_record_id": supplier_hotel_record_id}
        )

        return result.mappings().all()

    async def find_exact_name_master_candidates(self, supplier_hotel_record_id: int):
        """
        Name-first candidate search, with no 1 km wall.

        The geo query above can only ever return masters within 1 km, so a hotel
        whose suppliers disagree about its coordinates by more than that never
        had its name compared to anything — it silently became a second master.
        This asks the other question: is there a master anywhere nearby that is
        called exactly the same thing?

        Both blocking keys are used because neither subsumes the other:
        core_name catches "The Residency Chennai" against "The Residency";
        strict_name catches the pairs core_name loses when the two records
        disagree on the city spelling (Gurgaon/Gurugram, Calicut/Kozhikode) and
        core stripping therefore removes different tokens from each side.

        The exclusions below are deliberately identical to the geo query's. This
        pass widens the search radius, not the rules — a reviewer's split
        decision and the one-record-per-supplier invariant must survive it.
        """
        result = await self.session.execute(
            text(
                """
                SELECT
                    s.id AS supplier_hotel_record_id,
                    s.supplier_name,
                    s.supplier_hotel_id,
                    s.hotel_name AS supplier_hotel_name,
                    s.normalized_name AS supplier_normalized_name,
                    s.address AS supplier_normalized_address,
                    s.star_rating AS supplier_star_rating,
                    s.city AS supplier_city,
                    s.state AS supplier_state,
                    s.postal_code AS supplier_postal_code,

                    m.master_hotel_id,
                    m.hotel_name AS master_hotel_name,
                    m.normalized_name AS master_normalized_name,
                    m.address AS master_normalized_address,
                    m.star_rating AS master_star_rating,
                    m.city AS master_city,
                    m.state AS master_state,
                    m.postal_code AS master_postal_code,

                    s.city,
                    s.country,

                    ST_Distance(m.geo_location, COALESCE(s.match_geo_location, s.geo_location)) AS distance_meters

                FROM supplier_hotels s
                JOIN master_hotels m
                  ON LOWER(m.country) = LOWER(s.country)

                WHERE s.id = :supplier_hotel_record_id
                  AND COALESCE(s.match_geo_location, s.geo_location) IS NOT NULL
                  AND m.geo_location IS NOT NULL

                  AND (
                        (    m.core_name IS NOT NULL
                         AND btrim(m.core_name) <> ''
                         AND lower(m.core_name) = lower(s.core_name)
                         AND ST_DWithin(m.geo_location, COALESCE(s.match_geo_location, s.geo_location), :core_radius))
                     OR (    m.strict_name IS NOT NULL
                         AND btrim(m.strict_name) <> ''
                         AND lower(m.strict_name) = lower(s.strict_name)
                         AND ST_DWithin(m.geo_location, COALESCE(s.match_geo_location, s.geo_location), :strict_radius))
                  )

                  -- Beyond the geo query's reach only. Anything inside 1 km is
                  -- already a candidate there and must keep being decided by the
                  -- existing tiers, unchanged.
                  AND ST_Distance(m.geo_location, COALESCE(s.match_geo_location, s.geo_location)) > 1000

                  AND NOT EXISTS (
                      SELECT 1
                      FROM hotel_mappings hm
                      WHERE hm.master_hotel_id = m.master_hotel_id
                        AND hm.supplier_name = s.supplier_name
                        AND hm.supplier_hotel_id <> s.supplier_hotel_id
                  )

                  AND NOT EXISTS (
                      SELECT 1
                      FROM master_non_merge_assertion nm
                      JOIN hotel_mappings hm2
                        ON hm2.master_hotel_id = m.master_hotel_id
                      WHERE (nm.row_id_a = s.id AND nm.row_id_b = hm2.supplier_hotel_row_id)
                         OR (nm.row_id_b = s.id AND nm.row_id_a = hm2.supplier_hotel_row_id)
                  )

                ORDER BY distance_meters ASC
                LIMIT 10;
                """
            ),
            {
                "supplier_hotel_record_id": supplier_hotel_record_id,
                "core_radius": CORE_ONLY_NAME_REVIEW_METERS,
                "strict_radius": EXACT_NAME_REVIEW_METERS,
            }
        )

        return result.mappings().all()

    async def supplier_id_collision(self, supplier_hotel) -> str | None:
        """
        Detect supplier hotel ids that are not unique in the source feed.

        Two cases, and they are materially different:

        DUPLICATE_SUPPLIER_ROW    — the rows describe ONE property. A duplicate
                                    line in the supplier file. Keep one, flag the
                                    rest.
        SUPPLIER_ID_COLLISION     — the rows describe DIFFERENT properties. The
                                    supplier has reused one id for two hotels, so
                                    any downstream lookup by that id is ambiguous
                                    and could resolve a booking to the wrong
                                    hotel. This must be surfaced to the supplier;
                                    it cannot be repaired here.

        The two are separated on name similarity and distance, not on string
        equality. Equality was brittle in both directions: "Hotel Mamallaa
        Heritage" and "Mamallaa Heritage Hotel" are one hotel 40 m apart and were
        discarded as a collision, while rows with byte-identical names 28 km
        apart were deduplicated as if they were the same line. 21 of 89 collision
        groups were one property listed twice under a name variant.
        """
        result = await self.session.execute(
            text(
                """
                WITH grp AS (
                    SELECT id, hotel_name, core_name, geo_location
                    FROM supplier_hotels
                    WHERE supplier_name = :supplier_name
                      AND supplier_hotel_id = :supplier_hotel_id
                ),
                pairs AS (
                    SELECT similarity(
                               lower(coalesce(a.core_name, a.hotel_name)),
                               lower(coalesce(b.core_name, b.hotel_name))
                           ) AS name_sim,
                           CASE WHEN a.geo_location IS NULL
                                  OR b.geo_location IS NULL THEN NULL
                                ELSE ST_Distance(a.geo_location, b.geo_location)
                           END AS apart_m
                    FROM grp a JOIN grp b ON b.id > a.id
                )
                SELECT (SELECT count(*) FROM grp) AS row_count,
                       coalesce(
                           bool_and(
                               -- identical name: one hotel at any distance
                               name_sim >= :sim_exact
                               -- strong name, close
                            OR (name_sim >= :sim_far  AND (apart_m IS NULL OR apart_m <= :m_far))
                               -- weaker name, co-located
                            OR (name_sim >= :sim_near AND apart_m IS NOT NULL AND apart_m <= :m_near)
                           ),
                           TRUE
                       ) AS one_property
                FROM pairs;
                """
            ),
            {
                "supplier_name": supplier_hotel.get("supplier_name"),
                "supplier_hotel_id": supplier_hotel.get("supplier_hotel_id"),
                "sim_exact": DUPLICATE_ROW_TIERS[0][1],
                "m_far": DUPLICATE_ROW_TIERS[1][0],
                "sim_far": DUPLICATE_ROW_TIERS[1][1],
                "m_near": DUPLICATE_ROW_TIERS[2][0],
                "sim_near": DUPLICATE_ROW_TIERS[2][1],
            }
        )

        row = result.mappings().first()

        if row is None or row["row_count"] <= 1:
            return None

        if not row["one_property"]:
            return "SUPPLIER_ID_COLLISION"

        # One property duplicated in the feed. Which copy survives used to be
        # whichever the workers happened to reach first, and the losing copies
        # often carried the better coordinate — 13 groups anchored their master
        # to a pin no other supplier agreed with. Choose deterministically
        # instead: the row corroborated by the most other suppliers wins.
        keeper = await self.session.execute(
            text(
                """
                WITH grp AS (
                    SELECT id, hotel_name, geo_location
                    FROM supplier_hotels
                    WHERE supplier_name = :supplier_name
                      AND supplier_hotel_id = :supplier_hotel_id
                )
                SELECT g.id
                FROM grp g
                ORDER BY (
                    SELECT count(*)
                    FROM supplier_hotels o
                    WHERE o.supplier_name <> :supplier_name
                      AND o.geo_location IS NOT NULL
                      AND g.geo_location IS NOT NULL
                      AND ST_DWithin(o.geo_location, g.geo_location, 300)
                      AND similarity(lower(o.hotel_name), lower(g.hotel_name)) > 0.6
                ) DESC,
                g.id ASC
                LIMIT 1;
                """
            ),
            {
                "supplier_name": supplier_hotel.get("supplier_name"),
                "supplier_hotel_id": supplier_hotel.get("supplier_hotel_id"),
            }
        )

        keeper_id = keeper.scalar()

        if keeper_id == supplier_hotel.get("id"):
            return None

        return "DUPLICATE_SUPPLIER_ROW"

    def score_candidate(self, candidate):
        score_result = calculate_rule_based_score(
            distance_meters=candidate["distance_meters"],
            master_hotel_name=candidate["master_hotel_name"],
            supplier_hotel_name=candidate["supplier_hotel_name"],
            master_address=candidate["master_normalized_address"],
            supplier_address=candidate["supplier_normalized_address"],
            master_city=candidate["master_city"],
            supplier_city=candidate["supplier_city"],
            master_state=candidate["master_state"],
            supplier_state=candidate["supplier_state"],
            master_postal_code=candidate["master_postal_code"],
            supplier_postal_code=candidate["supplier_postal_code"],
            rarest_shared_token_df=self.rarest_shared_token_df(
                candidate["master_hotel_name"],
                candidate["supplier_hotel_name"],
            ),
            # Read from the cache the async loader filled; the corpus edges are
            # the same for every candidate of this record.
            rarity_cutoffs=self._rarity_cutoffs,
        )

        return {
            "supplier_hotel_record_id": candidate["supplier_hotel_record_id"],
            "supplier_name": candidate["supplier_name"],
            "supplier_hotel_id": candidate["supplier_hotel_id"],
            "supplier_hotel_name": candidate["supplier_hotel_name"],
             
             
             "supplier_normalized_name":
                candidate["supplier_normalized_name"],

            "supplier_normalized_address":
                candidate["supplier_normalized_address"],
                
            "master_hotel_id": candidate["master_hotel_id"],
            "master_hotel_name": candidate["master_hotel_name"],

            "city": candidate["city"],
            "country": candidate["country"],
            "distance_meters": float(candidate["distance_meters"]),

            "score": {
                "geo_score": score_result["geo_score"],
                "name_similarity": score_result["name_similarity"],
                "name_similarity_strict": score_result["name_similarity_strict"],
                "name_score": score_result["name_score"],
                "address_similarity": score_result["address_similarity"],
                "address_score": score_result["address_score"],
                "building_score": score_result["building_score"],
                "name_rarity_score": score_result["name_rarity_score"],
                "tier_passed": score_result["tier_passed"],
                "exact_name_class": score_result["exact_name_class"],
                "confidence_tier": score_result["confidence_tier"],
                "distance_meters": score_result["distance_meters"],
                "rule_score": score_result["final_score"],
                "rule_decision": score_result["decision"]
            }
        }

    async def score_supplier_hotel(self, supplier_hotel_record_id: int):
        supplier_hotel = await self.get_supplier_hotel(supplier_hotel_record_id)

        if supplier_hotel is None:
            return {
                "supplier_hotel_record_id": supplier_hotel_record_id,
                "supplier_hotel": None,
                "candidates": [],
                "best_candidate": None,
                "rule_decision": "SUPPLIER_NOT_FOUND"
            }

        # Corpus word frequencies for this record's name, loaded once here and
        # reused by every candidate comparison below.
        await self.load_token_frequencies(supplier_hotel["hotel_name"])
        await self.rarity_cutoffs()

        # A record that cannot be verified must not silently become a master.
        flag_reason = check_record_completeness(supplier_hotel)

        if flag_reason is None:
            collision = await self.supplier_id_collision(supplier_hotel)

            if collision is not None:
                flag_reason = collision

        if flag_reason is not None:
            return {
                "supplier_hotel_record_id": supplier_hotel_record_id,
                "supplier_hotel": dict(supplier_hotel),
                "candidates": [],
                "best_candidate": None,
                "flag_reason": flag_reason,
                "rule_decision": "FLAGGED"
            }

        candidates = list(
            await self.find_candidate_master_hotels(supplier_hotel_record_id)
        )

        # The name-first pass runs unconditionally, not as a fallback when the
        # geo pass finds nothing. The fragmentation case has a perfectly good
        # near neighbour inside 1 km — a different wing, a different property
        # entirely — so waiting for the geo pass to come up empty would miss it.
        seen_master_ids = {
            candidate["master_hotel_id"] for candidate in candidates
        }

        for candidate in await self.find_exact_name_master_candidates(
            supplier_hotel_record_id
        ):
            if candidate["master_hotel_id"] not in seen_master_ids:
                candidates.append(candidate)
                seen_master_ids.add(candidate["master_hotel_id"])

        if not candidates:
            return {
                "supplier_hotel_record_id": supplier_hotel_record_id,
                "supplier_hotel": dict(supplier_hotel),
                "candidates": [],
                "best_candidate": None,
                "rule_decision": "CREATE_NEW_MASTER"
            }

        scored_candidates = [
            self.score_candidate(candidate)
            for candidate in candidates
        ]

        # Candidates that clear the distance/name tier gate always outrank those
        # that do not, so a merely-nearby hotel can never win over a genuine
        # name match on composite score alone. Among those that fail the gate, an
        # exact name outranks the rest: it is the one worth putting in front of a
        # person, and it must not lose the top slot to a closer stranger.
        #
        # That exact-name preference applies ONLY to candidates that failed the
        # gate. Applied to those that passed it silently outranked the composite
        # score, and geography stopped mattering at all: ClearTrip's "Zone
        # Connect by The Park Coimbatore" sat 9 m from one master and 823 m from
        # another, scored 87.83 against the near one and 74.57 against the far
        # one, and was mapped to the far one purely because that master's name
        # matched character for character. Among candidates that already cleared
        # the gate the composite has weighed name and distance together, which is
        # the whole point of scoring them.
        scored_candidates = sorted(
            scored_candidates,
            key=lambda item: (
                item["score"]["tier_passed"],
                (not item["score"]["tier_passed"])
                and item["score"]["exact_name_class"] is not None,
                item["score"]["rule_score"],
            ),
            reverse=True
        )

        best_candidate = scored_candidates[0]

        return {
            "supplier_hotel_record_id": supplier_hotel_record_id,
            "supplier_hotel": dict(supplier_hotel),
            "candidates": scored_candidates,
            "best_candidate": best_candidate,
            "rule_decision": best_candidate["score"]["rule_decision"]
        }