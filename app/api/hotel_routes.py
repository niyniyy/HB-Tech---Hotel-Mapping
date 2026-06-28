import os
import shutil
import tempfile
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.connection import get_db
from app.services.import_service import ImportService
from app.services.mapping_status_service import MappingStatusService
from app.services.manual_review_service import ManualReviewService
from app.models.schemas import (
    ImportSummary,
    MappingStatusResponse,
    SuggestedMatchesResponse,
    ManualReviewResponse,
    ManualReviewDetail,
)
import logging
from app.services.suggested_matches_service import SuggestedMatchesService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Hotels"])


# ─────────────────────────────────────────────────────────────
# ENDPOINT 1: Import Supplier Hotels
# POST /api/v1/hotels/import
# Person B owns this endpoint
# ─────────────────────────────────────────────────────────────
@router.post("/hotels/import", response_model=ImportSummary)
async def import_supplier_hotels(
    supplier_name: str = Form(..., description="Supplier name e.g. Sabre, ClearTrip"),
    file: UploadFile = File(..., description="Standardized CSV file"),
    db: AsyncSession = Depends(get_db)
):
    """
    Import a supplier hotel CSV file into the supplier_hotels table.

    Steps:
    1. Save uploaded file to temp directory
    2. Read and validate CSV
    3. Insert in batches of 1000
    4. Compute geo_location from lat/lon
    5. Add all hotels to hotel_mapping_queue with status=Pending
    6. Return import summary
    """
    # Validate file type
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported")

    # Save uploaded file to temp directory
    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        service = ImportService(db)
        result = await service.import_from_csv(tmp_path, supplier_name)
        return result

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Import failed: {e}")
        raise HTTPException(status_code=500, detail="Import failed. Check logs for details.")
    finally:
        # Always clean up temp file
        os.unlink(tmp_path)


# ─────────────────────────────────────────────────────────────
# ENDPOINT 7: Get Mapping Status
# GET /api/v1/mapping/status
# Person B owns this endpoint
# ─────────────────────────────────────────────────────────────
@router.get("/mapping/status", response_model=MappingStatusResponse)
async def get_mapping_status(
    db: AsyncSession = Depends(get_db)
):
    """
    Returns counts of hotel_mapping_queue records grouped by status.

    Statuses:
    - Pending: waiting to be processed
    - Processing: currently being matched
    - Completed: successfully matched
    - Failed: matching failed (will retry)
    - ManualReview: needs human review (score 75-90)
    """
    service = MappingStatusService(db)
    return await service.get_status()


@router.get(
    "/hotels/{supplier_hotel_id}/matches",
    response_model=SuggestedMatchesResponse
)
async def get_suggested_matches(
    supplier_hotel_id: int,
    db: AsyncSession = Depends(get_db)
):

    service = SuggestedMatchesService(db)

    matches = await service.get_suggestions(
        supplier_hotel_id
    )

    return {
        "matches": matches
    }
    

# ─────────────────────────────────────────────────────────────
# ENDPOINT: Get Manual Review Queue
# GET /api/v1/manual-review
# ─────────────────────────────────────────────────────────────
@router.get(
    "/manual-review",
    response_model=ManualReviewResponse
)
async def get_manual_review_queue(
    limit: int = 100,
    db: AsyncSession = Depends(get_db)
):
    """
    Returns hotels awaiting manual review.

    Optional query parameter:
    - limit (default = 100)
    """

    service = ManualReviewService(db)

    reviews = await service.get_manual_reviews(limit)

    return {
        "reviews": reviews
    }
    
@router.get(
    "/manual-review/{supplier_hotel_id}",
    response_model=ManualReviewDetail
)
async def get_manual_review(
    supplier_hotel_id: int,
    db: AsyncSession = Depends(get_db)
):
    """
    Returns one hotel awaiting manual review.
    """

    service = ManualReviewService(db)

    review = await service.get_manual_review(
        supplier_hotel_id
    )

    if review is None:
        raise HTTPException(
            status_code=404,
            detail="Manual review not found."
        )

    return review 
