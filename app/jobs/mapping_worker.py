import asyncio

from app.jobs.celery_app import celery_app
from app.database.connection import AsyncSessionLocal
from app.services.queue_processing_service import QueueProcessingService
from app.matching.master_embedding_service import MasterEmbeddingService


@celery_app.task
def test_worker():
    print("Worker executed successfully")
    return "SUCCESS"


@celery_app.task
def process_queue_batch(
    limit: int = 1000,
    apply_decision: bool = True
):
    async def run():
        async with AsyncSessionLocal() as db:
            service = QueueProcessingService(db)

            print("Starting queue processing")

            result = await service.process_pending_batch(
                limit=limit,
                apply_decision=apply_decision
            )

            processed = result["processed_count"]

            print(f"Processed: {processed}")

            return processed

    total_processed = asyncio.run(run())

    print(f"Finished processing {total_processed} hotels")

    return {
        "processed": total_processed
    }
    
@celery_app.task
def generate_master_embeddings():

    async def run():

        async with AsyncSessionLocal() as db:

            service = MasterEmbeddingService(db)

            print("Starting master embedding generation")

            result = await service.generate_master_embeddings()

            print(
                f"Generated {result['generated_embeddings']} "
                f"master hotel embeddings"
            )

            return result

    result = asyncio.run(run())

    return result