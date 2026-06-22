import asyncio
import json

from app.database.connection import AsyncSessionLocal
from app.services.matching_service import MatchingService
from app.services.queue_processing_service import QueueProcessingService


def print_json(data):
    print(json.dumps(data, indent=2, default=str))


async def test_matching_only():
    async with AsyncSessionLocal() as session:
        service = MatchingService(session)

        result = await service.score_supplier_hotel(62571)

        print("\n--- MatchingService Result ---")
        print_json(result)


async def test_queue_processing_no_db_write():
    async with AsyncSessionLocal() as session:
        service = QueueProcessingService(session)

        result = await service.process_one_supplier_hotel(
            supplier_hotel_record_id=62571,
            apply_decision=False
        )

        await session.rollback()

        print("\n--- QueueProcessingService Dry Run Result ---")
        print_json(result)


async def main():
    await test_matching_only()
    await test_queue_processing_no_db_write()


if __name__ == "__main__":
    asyncio.run(main())