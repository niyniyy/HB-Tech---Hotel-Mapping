import asyncio

from app.jobs.celery_app import celery_app
from app.database.connection import AsyncSessionLocal
from app.services.queue_processing_service import QueueProcessingService


@celery_app.task
def test_worker():
    print("Worker executed successfully")
    return "SUCCESS"


@celery_app.task
def process_queue_batch(limit: int = 10):

    async def run():
        async with AsyncSessionLocal() as db:
            service = QueueProcessingService(db)

            processed = await service.process_batch(limit)

            await db.commit()

            return processed

    processed = asyncio.run(run())

    print(f"Processed {processed} queue items")

    return {
        "processed": processed
    }