from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.manual_review_service import ManualReviewService
from app.services.master_hotel_service import MasterHotelService
from app.database.connection import get_db
from app.jobs.mapping_worker import process_queue_batch
from app.jobs.mapping_worker import generate_master_embeddings


router = APIRouter(
    prefix="/api/v1",
    tags=["Mapping"]
)

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
    
