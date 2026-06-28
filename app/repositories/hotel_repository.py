from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from app.models.hotel import SupplierHotel, HotelMappingQueue
import logging

logger = logging.getLogger(__name__)


class SupplierHotelRepository:
    """
    Handles all database operations for supplier_hotels table.
    Follows repository pattern — no business logic here.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def bulk_insert(self, hotels: list[dict]) -> list[int]:
        """
        Insert a batch of hotels into supplier_hotels.
        Returns count of inserted rows.
        """
        if not hotels:
            return []

        try:
            # Build SQLAlchemy insert objects
            hotel_objects = [SupplierHotel(**h) for h in hotels]
            self.db.add_all(hotel_objects)
            await self.db.flush()

            inserted_ids = [hotel.id for hotel in   hotel_objects]

            # Populate geo_location from lat/lon using PostGIS
            await self.db.execute(text("""
                UPDATE supplier_hotels
                SET geo_location = ST_SetSRID(
                    ST_MakePoint(longitude::float, latitude::float), 4326
                )::geography
                WHERE geo_location IS NULL
                AND latitude IS NOT NULL
                AND longitude IS NOT NULL
            """))

            return inserted_ids

        except Exception as e:
            logger.error(f"Bulk insert failed: {e}")
            raise

    async def get_by_id(self, hotel_id: int) -> SupplierHotel | None:
        result = await self.db.execute(
            select(SupplierHotel).where(SupplierHotel.id == hotel_id)
        )
        return result.scalar_one_or_none()

    async def get_by_supplier(self, supplier_name: str, limit: int = 100) -> list[SupplierHotel]:
        result = await self.db.execute(
            select(SupplierHotel)
            .where(SupplierHotel.supplier_name == supplier_name)
            .limit(limit)
        )
        return result.scalars().all()

    async def count_by_supplier(self) -> dict:
        result = await self.db.execute(
            select(
                SupplierHotel.supplier_name,
                func.count(SupplierHotel.id).label("count")
            ).group_by(SupplierHotel.supplier_name)
        )
        return {row.supplier_name: row.count for row in result.all()}


class MappingQueueRepository:
    """
    Handles all database operations for hotel_mapping_queue table.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def bulk_enqueue(self, supplier_hotel_ids: list[int]) -> int:
        """
        Add multiple supplier hotels to the mapping queue with Pending status.
        """
        queue_items = [
            HotelMappingQueue(
                supplier_hotel_id=hotel_id,
                status="Pending"
            )
            for hotel_id in supplier_hotel_ids
        ]

        self.db.add_all(queue_items)
        await self.db.flush()

        return len(queue_items)

    async def get_pending_items(self, limit: int = 100) -> list[HotelMappingQueue]:
        """
        Fetch pending queue items for processing.
        """
        result = await self.db.execute(
            select(HotelMappingQueue)
            .where(HotelMappingQueue.status == "Pending")
            .limit(limit)
        )

        return result.scalars().all()

    async def mark_processing(self, queue_id: int) -> HotelMappingQueue | None:
        """
        Mark queue item as Processing.
        """
        item = await self.db.get(HotelMappingQueue, queue_id)

        if item:
            item.status = "Processing"
            await self.db.flush()

        return item

    async def mark_completed(self, queue_id: int) -> HotelMappingQueue | None:
        """
        Mark queue item as Completed.
        """
        item = await self.db.get(HotelMappingQueue, queue_id)

        if item:
            item.status = "Completed"
            await self.db.flush()

        return item

    async def mark_failed(self, queue_id: int) -> HotelMappingQueue | None:
        """
        Mark queue item as Failed and increment retry count.
        """
        item = await self.db.get(HotelMappingQueue, queue_id)

        if item:
            item.status = "Failed"
            item.retry_count += 1
            await self.db.flush()

        return item

    async def get_status_counts(self) -> dict:
        """
        Returns count of queue items by status.
        Used by Get Mapping Status API.
        """
        result = await self.db.execute(
            select(
                HotelMappingQueue.status,
                func.count(HotelMappingQueue.id).label("count")
            ).group_by(HotelMappingQueue.status)
        )

        rows = result.all()
        counts = {row.status: row.count for row in rows}

        return {
            "total": sum(counts.values()),
            "pending": counts.get("Pending", 0),
            "processing": counts.get("Processing", 0),
            "completed": counts.get("Completed", 0),
            "failed": counts.get("Failed", 0),
            "manual_review": counts.get("ManualReview", 0),
        }