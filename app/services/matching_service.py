from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.matching.matcher import calculate_rule_based_score


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

                    m.master_hotel_id,
                    m.hotel_name AS master_hotel_name,
                    m.normalized_name AS master_normalized_name,
                    m.address AS master_normalized_address,
                    m.star_rating AS master_star_rating,

                    s.city,
                    s.country,

                    ST_Distance(m.geo_location, s.geo_location) AS distance_meters

                FROM supplier_hotels s
                JOIN master_hotels m
                  ON LOWER(m.country) = LOWER(s.country)
                 AND LOWER(m.city) = LOWER(s.city)

                WHERE s.id = :supplier_hotel_record_id
                  AND s.geo_location IS NOT NULL
                  AND m.geo_location IS NOT NULL
                  AND ST_DWithin(m.geo_location, s.geo_location, 300)

                ORDER BY distance_meters ASC
                LIMIT 10;
                """
            ),
            {"supplier_hotel_record_id": supplier_hotel_record_id}
        )

        return result.mappings().all()

    def score_candidate(self, candidate):
        score_result = calculate_rule_based_score(
            distance_meters=candidate["distance_meters"],
            master_normalized_name=candidate["master_normalized_name"],
            supplier_normalized_name=candidate["supplier_normalized_name"],
            master_address=candidate["master_normalized_address"],
            supplier_address=candidate["supplier_normalized_address"],
            master_star_rating=candidate["master_star_rating"],
            supplier_star_rating=candidate["supplier_star_rating"],
            master_chain_name=None,
            supplier_chain_name=None
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
                "name_score": score_result["name_score"],
                "address_similarity": score_result["address_similarity"],
                "address_score": score_result["address_score"],
                "star_score": score_result["star_score"],
                "chain_score": score_result["chain_score"],
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

        candidates = await self.find_candidate_master_hotels(
            supplier_hotel_record_id
        )

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

        best_candidate = max(
            scored_candidates,
            key=lambda item: item["score"]["rule_score"]
        )

        return {
            "supplier_hotel_record_id": supplier_hotel_record_id,
            "supplier_hotel": dict(supplier_hotel),
            "candidates": scored_candidates,
            "best_candidate": best_candidate,
            "rule_decision": best_candidate["score"]["rule_decision"]
        }