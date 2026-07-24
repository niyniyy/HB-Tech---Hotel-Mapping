from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.manual_review_service import ManualReviewService
from app.services.master_hotel_service import MasterHotelService
from app.services.pipeline_reset_service import PipelineResetService, CONFIRM_TOKEN
from app.database.connection import get_db
from app.jobs.mapping_worker import process_queue_batch
from app.jobs.mapping_worker import generate_master_embeddings


router = APIRouter(
    prefix="/api/v1",
    tags=["Mapping"]
)


class ResetRequest(BaseModel):
    confirm: str = ""
    actor: str = "reviewer"


class BatchDecisionRequest(BaseModel):
    # Explicit ids, never a filter. A batch is the reviewer acting on rows they
    # have seen; a filter would let the UI decide about records nobody looked at.
    supplier_hotel_ids: list[int]
    action: str
    reason: str | None = None
    actor: str = "reviewer"


class AttachRequest(BaseModel):
    master_hotel_id: int
    reason: str | None = None
    actor: str = "reviewer"

@router.post("/embeddings/generate-masters")
async def generate_master_hotel_embeddings():

    task = generate_master_embeddings.delay()

    return {
        "message": "Master hotel embedding generation started.",
        "task_id": task.id
    }
    
@router.post("/mapping/run")
async def run_mapping(
    limit: int = 1000,
    apply_decision: bool = True,
    session: AsyncSession = Depends(get_db)
):
    """
    Process all pending queue records in batches.

    apply_decision=True updates the database based on
    the final mapping decision.
    """

    task = process_queue_batch.delay(
        limit=limit,
        apply_decision=apply_decision
    )

    return {
        "message": "Queue processing started.",
        "task_id": task.id
    }

@router.get("/mapping/reset/preview")
async def preview_mapping_reset(session: AsyncSession = Depends(get_db)):
    """What a reset would clear and what it would keep."""
    return await PipelineResetService(session).preview()


@router.post("/mapping/reset")
async def reset_mapping_pipeline(
    payload: ResetRequest,
    session: AsyncSession = Depends(get_db)
):
    """
    Clear all mapping output and re-queue the imported records.

    Requires the confirmation token in the body: the endpoint is directly
    callable, so nothing in the UI can be the safeguard.
    """
    if payload.confirm != CONFIRM_TOKEN:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Confirmation required — send {{\"confirm\": \"{CONFIRM_TOKEN}\"}} "
                "to clear all mapping output."
            ),
        )

    return await PipelineResetService(session).reset(payload.actor)


@router.get("/mapping/statistics")
async def get_mapping_statistics(session: AsyncSession = Depends(get_db)):
    print("===== STATISTICS ENDPOINT HIT =====", flush=True)

    statistics = await MasterHotelService(session).get_mapping_statistics()

    return statistics

@router.get("/mapping/supplier-statistics")
async def get_supplier_statistics(
    session: AsyncSession = Depends(get_db)
):

    service = MasterHotelService(session)

    suppliers = await service.get_supplier_statistics()

    return {
        "suppliers": suppliers
    }
    

@router.post("/manual-review/batch")
async def batch_decide_manual_review(
    payload: BatchDecisionRequest,
    session: AsyncSession = Depends(get_db)
):
    """Apply approve or reject to a set of queued records the reviewer selected."""
    result = await ManualReviewService(session).decide_batch(
        supplier_hotel_ids=payload.supplier_hotel_ids,
        action=payload.action,
        reason=payload.reason,
        actor=payload.actor,
    )

    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])

    return result


@router.get("/manual-review/{supplier_hotel_id}/master-candidates")
async def manual_review_master_candidates(
    supplier_hotel_id: int,
    q: str | None = None,
    limit: int = 20,
    session: AsyncSession = Depends(get_db)
):
    """
    Masters a reviewer might attach this record to, nearest first.

    Seeded from the record itself so the right answer is usually already on
    screen: a reviewer made to type a search before seeing any option is being
    asked to remember what the system already knows.
    """
    return await ManualReviewService(session).master_candidates(
        supplier_hotel_id, query=q, limit=max(1, min(limit, 50))
    )


@router.post("/manual-review/{supplier_hotel_id}/attach")
async def attach_manual_review(
    supplier_hotel_id: int,
    payload: AttachRequest,
    session: AsyncSession = Depends(get_db)
):
    """Attach a queued record to a master the reviewer chose."""
    result = await ManualReviewService(session).attach_to_existing_master(
        supplier_hotel_id=supplier_hotel_id,
        master_hotel_id=payload.master_hotel_id,
        reason=payload.reason,
        actor=payload.actor,
    )

    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])

    return result


@router.post("/manual-review/{supplier_hotel_id}/approve")
async def approve_manual_review(
    supplier_hotel_id: int,
    session: AsyncSession = Depends(get_db)
):
    service = ManualReviewService(session)

    success = await service.approve_match(
        supplier_hotel_id
    )
    
    if not success:
        raise HTTPException(
            status_code=404,
            detail="Manual review record not found."
        )
    
    return {
        "success": success
    }
    
    
@router.post("/manual-review/{supplier_hotel_id}/create-master")
async def create_new_master(
    supplier_hotel_id: int,
    session: AsyncSession = Depends(get_db)
):
    service = ManualReviewService(session)

    success = await service.create_new_master(
        supplier_hotel_id
    )
    if not success:
        raise HTTPException(
            status_code=404,
            detail="Manual review record not found."
        )

    return {
        "success": success
    }

@router.post("/manual-review/{supplier_hotel_id}/reject")
async def reject_manual_review(
    supplier_hotel_id: int,
    session: AsyncSession = Depends(get_db)
):
    service = ManualReviewService(session)

    success = await service.reject_review(
        supplier_hotel_id
    )
    
    if not success:
        raise HTTPException(
            status_code=404,
            detail="Manual review record not found."
        )

    return {
        "success": success
    }
    

@router.get("/master-hotels/search")
async def search_master_hotels(
    q: str,
    limit: int = 50,
    session: AsyncSession = Depends(get_db)
):

    service = MasterHotelService(session)

    results = await service.search_master_hotels(
        q,
        limit
    )

    return {
        "query": q,
        "count": len(results),
        "results": results
    }

@router.get("/master-hotels/{master_hotel_id}")
async def get_master_hotel(
    master_hotel_id: int,
    session: AsyncSession = Depends(get_db)
):

    service = MasterHotelService(session)

    hotel = await service.get_master_hotel(
        master_hotel_id
    )
    
    if hotel is None:
        raise HTTPException(
            status_code=404,
            detail="Master hotel not found."
        )

    return hotel

@router.get("/master-hotels/{master_hotel_id}/mappings")
async def get_master_hotel_mappings(
    master_hotel_id: int,
    session: AsyncSession = Depends(get_db)
):

    service = MasterHotelService(session)

    mappings = await service.get_master_hotel_mappings(
        master_hotel_id
    )
    
    return {
        "master_hotel_id": master_hotel_id,
        "mapping_count": len(mappings),
        "mapped_hotels": mappings
}
    
