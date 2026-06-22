from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.matching_service import MatchingService


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

    async def get_pending_queue_ids(self, limit: int = 10):
        result = await self.session.execute(
            text(
                """
                SELECT supplier_hotel_record_id
                FROM hotel_mapping_queue
                WHERE status = 'Pending'
                ORDER BY supplier_hotel_record_id
                LIMIT :limit;
                """
            ),
            {"limit": limit}
        )

        return [row[0] for row in result.fetchall()]

    async def mark_queue_status(
        self,
        supplier_hotel_record_id: int,
        status: str
    ):
        await self.session.execute(
            text(
                """
                UPDATE hotel_mapping_queue
                SET status = :status
                WHERE supplier_hotel_record_id = :supplier_hotel_record_id;
                """
            ),
            {
                "status": status,
                "supplier_hotel_record_id": supplier_hotel_record_id
            }
        )

    async def create_master_hotel_from_supplier(self, supplier_hotel):
        result = await self.session.execute(
            text(
                """
                INSERT INTO master_hotels (
                    hotel_name,
                    normalized_name,
                    address,
                    normalized_address,
                    city,
                    country,
                    postal_code,
                    star_rating,
                    latitude,
                    longitude,
                    geo_location
                )
                VALUES (
                    :hotel_name,
                    :normalized_name,
                    :address,
                    :normalized_address,
                    :city,
                    :country,
                    :postal_code,
                    :star_rating,
                    :latitude,
                    :longitude,
                    CASE
                        WHEN :latitude IS NOT NULL AND :longitude IS NOT NULL
                        THEN ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography
                        ELSE NULL
                    END
                )
                RETURNING master_hotel_id;
                """
            ),
            {
                "hotel_name": supplier_hotel.get("hotel_name"),
                "normalized_name": supplier_hotel.get("normalized_name"),
                "address": supplier_hotel.get("address"),
                "normalized_address": supplier_hotel.get("normalized_address"),
                "city": supplier_hotel.get("city"),
                "country": supplier_hotel.get("country"),
                "postal_code": supplier_hotel.get("postal_code"),
                "star_rating": supplier_hotel.get("star_rating"),
                "latitude": supplier_hotel.get("latitude"),
                "longitude": supplier_hotel.get("longitude")
            }
        )

        return result.scalar_one()

    async def insert_hotel_mapping(
        self,
        master_hotel_id: int,
        supplier_hotel,
        match_score: float,
        mapping_type: str,
        is_manual_verified: bool = False
    ):
        await self.session.execute(
            text(
                """
                INSERT INTO hotel_mappings (
                    master_hotel_id,
                    supplier_hotel_record_id,
                    supplier_name,
                    supplier_hotel_id,
                    match_score,
                    mapping_type,
                    is_manual_verified
                )
                VALUES (
                    :master_hotel_id,
                    :supplier_hotel_record_id,
                    :supplier_name,
                    :supplier_hotel_id,
                    :match_score,
                    :mapping_type,
                    :is_manual_verified
                );
                """
            ),
            {
                "master_hotel_id": master_hotel_id,
                "supplier_hotel_record_id": supplier_hotel.get("id"),
                "supplier_name": supplier_hotel.get("supplier_name"),
                "supplier_hotel_id": supplier_hotel.get("supplier_hotel_id"),
                "match_score": match_score,
                "mapping_type": mapping_type,
                "is_manual_verified": is_manual_verified
            }
        )

    async def apply_rule_decision(self, match_result):
        supplier_hotel = match_result["supplier_hotel"]
        supplier_hotel_record_id = match_result["supplier_hotel_record_id"]
        rule_decision = match_result["rule_decision"]
        best_candidate = match_result["best_candidate"]

        if rule_decision == "SUPPLIER_NOT_FOUND":
            return {
                "supplier_hotel_record_id": supplier_hotel_record_id,
                "status": "FAILED",
                "message": "Supplier hotel not found"
            }

        if rule_decision == "AUTO_MATCH":
            rule_score = best_candidate["score"]["rule_score"]

            await self.insert_hotel_mapping(
                master_hotel_id=best_candidate["master_hotel_id"],
                supplier_hotel=supplier_hotel,
                match_score=rule_score,
                mapping_type="AUTO",
                is_manual_verified=False
            )

            await self.mark_queue_status(
                supplier_hotel_record_id,
                "Completed"
            )

            return {
                "supplier_hotel_record_id": supplier_hotel_record_id,
                "status": "Completed",
                "decision": "AUTO_MATCH",
                "best_candidate": best_candidate
            }

        if rule_decision == "MANUAL_REVIEW":
            await self.mark_queue_status(
                supplier_hotel_record_id,
                "ManualReview"
            )

            return {
                "supplier_hotel_record_id": supplier_hotel_record_id,
                "status": "ManualReview",
                "decision": "MANUAL_REVIEW",
                "best_candidate": best_candidate
            }

        if rule_decision == "CREATE_NEW_MASTER":
            new_master_hotel_id = await self.create_master_hotel_from_supplier(
                supplier_hotel
            )

            await self.insert_hotel_mapping(
                master_hotel_id=new_master_hotel_id,
                supplier_hotel=supplier_hotel,
                match_score=100.0,
                mapping_type="NEW_MASTER",
                is_manual_verified=True
            )

            await self.mark_queue_status(
                supplier_hotel_record_id,
                "Completed"
            )

            return {
                "supplier_hotel_record_id": supplier_hotel_record_id,
                "status": "Completed",
                "decision": "CREATE_NEW_MASTER",
                "new_master_hotel_id": new_master_hotel_id
            }

        return {
            "supplier_hotel_record_id": supplier_hotel_record_id,
            "status": "FAILED",
            "message": f"Unknown decision: {rule_decision}"
        }

    async def process_one_supplier_hotel(
        self,
        supplier_hotel_record_id: int,
        apply_decision: bool = False
    ):
        await self.mark_queue_status(
            supplier_hotel_record_id,
            "Processing"
        )

        match_result = await self.matching_service.score_supplier_hotel(
            supplier_hotel_record_id
        )

        if not apply_decision:
            await self.mark_queue_status(
                supplier_hotel_record_id,
                "Pending"
            )

            return match_result

        final_result = await self.apply_rule_decision(match_result)

        return {
            "match_result": match_result,
            "final_result": final_result
        }

    async def process_pending_batch(
        self,
        limit: int = 10,
        apply_decision: bool = False
    ):
        pending_ids = await self.get_pending_queue_ids(limit)

        results = []

        for supplier_hotel_record_id in pending_ids:
            try:
                result = await self.process_one_supplier_hotel(
                    supplier_hotel_record_id,
                    apply_decision=apply_decision
                )

                results.append(result)

            except Exception as error:
                await self.session.rollback()

                results.append({
                    "supplier_hotel_record_id": supplier_hotel_record_id,
                    "status": "FAILED",
                    "error": str(error)
                })

        await self.session.commit()

        return {
            "processed_count": len(results),
            "results": results
        }