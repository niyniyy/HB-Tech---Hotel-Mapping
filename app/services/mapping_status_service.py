from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.hotel_repository import MappingQueueRepository
from app.models.schemas import MappingStatusResponse
import logging

logger = logging.getLogger(__name__)


class MappingStatusService:
    """
    Handles Get Mapping Status API (Endpoint 7).
    Returns counts of queue items by status.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.queue_repo = MappingQueueRepository(db)

    async def get_status(self) -> MappingStatusResponse:
        counts = await self.queue_repo.get_status_counts()
        return MappingStatusResponse(**counts)
