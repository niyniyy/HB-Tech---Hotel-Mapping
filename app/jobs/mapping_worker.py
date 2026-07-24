import asyncio

from app.jobs.celery_app import celery_app
from app.database.connection import AsyncSessionLocal, engine
from app.services.queue_processing_service import QueueProcessingService
from app.matching.master_embedding_service import MasterEmbeddingService



async def _with_fresh_pool(coro_fn):
    """
    Run a task body, then drop the connection pool.

    `asyncio.run()` builds a new event loop per task, but the engine is a module
    global whose pooled asyncpg connections stay bound to the loop that created
    them. The second task in a worker process therefore inherits sockets tied to
    a closed loop and dies with "got Future attached to a different loop" —
    which is what stranded 117 rows in Processing mid-run. Disposing at the end
    means the next task opens its own connections on its own loop.
    """
    try:
        return await coro_fn()
    finally:
        await engine.dispose()


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

    total_processed = asyncio.run(_with_fresh_pool(run))

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

    result = asyncio.run(_with_fresh_pool(run))

    return result