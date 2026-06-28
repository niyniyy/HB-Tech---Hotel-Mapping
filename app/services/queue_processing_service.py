from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.matching_service import MatchingService
from app.matching.ai_integration_service import AIIntegrationService
from app.services.hotel_mapping_service import HotelMappingService
from typing import Any  

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

    async def get_pending_queue_ids(self, limit: int = 10):
        result = await self.session.execute(
            text(
                """
                SELECT supplier_hotel_id
                FROM hotel_mapping_queue
                WHERE status = 'Pending'
                ORDER BY supplier_hotel_id
                LIMIT :limit;
                """
            ),
            {"limit": limit}
        )

        return [row[0] for row in result.fetchall()]

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
                    decision_reason
                )
                VALUES (
                    :supplier_hotel_id,
                    :suggested_master_hotel_id,
                    :rule_score,
                    :ai_similarity,
                    :decision_reason
                );
                """
            ),
            {
                "supplier_hotel_id": supplier_hotel_record_id,
                "suggested_master_hotel_id": best_candidate["master_hotel_id"],
                "rule_score": score["rule_score"],
                "ai_similarity": score.get("ai_similarity"),
                "decision_reason": score.get("decision_reason"),
            }
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

        if decision == "AUTO_MATCH":
            rule_score = best_candidate["score"]["rule_score"]

            await self.mapping_service.insert_hotel_mapping(
                master_hotel_id=best_candidate["master_hotel_id"],
                supplier_hotel=supplier_hotel,
                match_score=rule_score,
                mapping_type="AUTO",
                is_manual_verified=False 
        )

            await self.mapping_service.update_queue_status(
                supplier_hotel_record_id,
                "Completed"
            )

            return {
                "supplier_hotel_record_id": supplier_hotel_record_id,
                "status": "Completed",
                "decision": "AUTO_MATCH",
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
                "new_master_hotel_id": new_master_hotel_id
            }

        return {
            "supplier_hotel_record_id": supplier_hotel_record_id,
            "status": "FAILED",
            "message": f"Unknown decision: {decision}"
        }

    async def process_one_supplier_hotel(
        self,
        supplier_hotel_record_id: int,
        apply_decision: bool = False
    ):
        await self.mapping_service.update_queue_status(
    supplier_hotel_record_id,
    "Processing"
)

        match_result = await self.matching_service.score_supplier_hotel(
            supplier_hotel_record_id
        )
        
        best_candidate = match_result.get("best_candidate")

        if best_candidate:

            score = best_candidate["score"]

            rule_score = score["rule_score"]

            if 70 <= rule_score < 90:

                enriched_candidate = await self.ai_service.enrich_candidate(
                    best_candidate
                )

                match_result["best_candidate"] = enriched_candidate

                match_result["final_decision"] = (
    enriched_candidate["score"]["final_decision"]
)

        if not apply_decision:
            await self.mapping_service.update_queue_status(
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

                try:
                    await self.mapping_service.update_queue_status(
                        supplier_hotel_record_id,
                        "Pending"
                    )
                    await self.session.commit()
                except Exception:
                    await self.session.rollback()

                results.append({
                    "supplier_hotel_record_id": supplier_hotel_record_id,
                    "status": "FAILED",
                    "error": str(error)
                })

        # Commit all successful work in the batch
        await self.session.commit()

        return {
            "processed_count": len(results),
            "results": results
        }