from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.manual_review_service import ManualReviewService
from app.services.master_hotel_service import MasterHotelService
from app.database.connection import get_db
from app.services.queue_processing_service import QueueProcessingService


router = APIRouter(
    prefix="/api/v1",
    tags=["Mapping"]
)


@router.post("/mapping/run")
async def run_mapping(
    limit: int = 10,
    apply_decision: bool = False,
    session: AsyncSession = Depends(get_db)
):
    """
    Run rule-based matching on pending queue records.

    apply_decision=False means dry run only.
    apply_decision=True means update DB based on rule decision.
    """

    service = QueueProcessingService(session)

    result = await service.process_pending_batch(
        limit=limit,
        apply_decision=apply_decision
    )

    return result

@router.get("/mapping/statistics")
async def get_mapping_statistics(
    session: AsyncSession = Depends(get_db)
):

    service = MasterHotelService(session)

    statistics = await service.get_mapping_statistics()

    return statistics
    

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
    
