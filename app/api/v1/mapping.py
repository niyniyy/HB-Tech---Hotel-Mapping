from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import get_async_session
from app.services.queue_processing_service import QueueProcessingService


router = APIRouter()


@router.post("/mapping/run")
async def run_mapping(
    limit: int = 10,
    apply_decision: bool = False,
    session: AsyncSession = Depends(get_async_session)
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