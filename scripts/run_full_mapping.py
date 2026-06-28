import asyncio
import time

from sqlalchemy import text

from app.database.connection import AsyncSessionLocal
from app.services.queue_processing_service import QueueProcessingService


# Number of hotels processed in each batch
BATCH_SIZE = 5000


async def get_statistics(session):
    """
    Fetch current processing statistics from the database.
    """

    result = await session.execute(
        text(
            """
            SELECT
                (SELECT COUNT(*) FROM supplier_hotels) AS supplier_hotels,

                (SELECT COUNT(*)
                 FROM hotel_mapping_queue
                 WHERE status = 'Pending') AS pending,

                (SELECT COUNT(*)
                 FROM hotel_mapping_queue
                 WHERE status = 'Completed') AS completed,

                (SELECT COUNT(*)
                 FROM hotel_mapping_queue
                 WHERE status = 'ManualReview') AS manual_review,

                (SELECT COUNT(*)
                 FROM hotel_mappings) AS mappings,

                (SELECT COUNT(*)
                 FROM master_hotels) AS master_hotels;
            """
        )
    )

    return dict(result.mappings().first())


def format_time(seconds: float) -> str:
    """
    Convert seconds to HH:MM:SS.
    """

    seconds = int(seconds)

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    return f"{hours:02}:{minutes:02}:{secs:02}"


async def main():

    print("\n==============================================================")
    print("           HOTEL MAPPING ENGINE")
    print("        Full Queue Processing Started")
    print("==============================================================\n")

    start_time = time.time()

    total_processed = 0
    batch_number = 1

    try:

        while True:
          
            batch_start = time.time()
            async with AsyncSessionLocal() as session:

                service = QueueProcessingService(session)

                result = await service.process_pending_batch(
                    limit=BATCH_SIZE,
                    apply_decision=True,
                )

                processed = result["processed_count"]

                if processed == 0:
                    break

                total_processed += processed

                successful = 0
                failed = 0
                failed_records = []

                for item in result["results"]:

                    if item.get("status") == "FAILED":

                        failed += 1

                        failed_records.append(
                            {
                                "id": item["supplier_hotel_record_id"],
                                "error": item["error"],
                            }
                        )

                    else:

                        successful += 1

                stats = await get_statistics(session)

                elapsed = time.time() - start_time

                speed = (
                    total_processed / elapsed
                    if elapsed > 0
                    else 0
                )

                total_hotels = stats["supplier_hotels"]
                completed = stats["completed"]
                pending = stats["pending"]

                progress = completed / total_hotels * 100

                eta = pending / speed if speed > 0 else 0

                print("=" * 70)
                print(f"Batch Number         : {batch_number}")
                print(f"Processed This Batch : {processed}")
                print(f"Successful           : {successful}")
                print(f"Failed               : {failed}")
                print(f"Total Processed      : {total_processed:,}")
                print()

                print("Current Database Status")
                print("-" * 70)

                print(f"Supplier Hotels      : {stats['supplier_hotels']:,}")
                print(f"Completed            : {stats['completed']:,}")
                print(f"Pending              : {stats['pending']:,}")
                print(f"Manual Review        : {stats['manual_review']:,}")
                print(f"Mappings             : {stats['mappings']:,}")
                print(f"Master Hotels        : {stats['master_hotels']:,}")
                print()
                
                batch_time = time.time() - batch_start
                avg_time = batch_time / processed if processed else 0
      
                print("Performance")
                print("-" * 70)

                print(f"Batch Time          : {format_time(batch_time)}")
                print(f"Avg Time/Hotel      : {avg_time:.3f} sec")
                print(f"Progress             : {progress:.2f}%")
                print(f"Processing Speed     : {speed:.2f} hotels/sec")
                print(f"Elapsed Time         : {format_time(elapsed)}")
                print(f"Estimated Time Left  : {format_time(eta)}")

                print("=" * 70)

                if failed_records:

                    print("\nFailed Records")
                    print("-" * 70)

                    for record in failed_records:

                        print(
                            f"Hotel ID {record['id']} -> {record['error']}"
                        )

                    print("=" * 70)

                print()

                batch_number += 1

    except KeyboardInterrupt:

        print("\n")
        print("=" * 70)
        print("Processing interrupted by user.")
        print("All completed batches have already been committed.")
        print("You can safely restart this script.")
        print("=" * 70)
        return

    elapsed = time.time() - start_time

    async with AsyncSessionLocal() as session:

        stats = await get_statistics(session)

    print("\n")
    print("=" * 70)
    print("               PROCESSING COMPLETE")
    print("=" * 70)

    print(f"Supplier Hotels : {stats['supplier_hotels']:,}")
    print(f"Completed       : {stats['completed']:,}")
    print(f"Pending         : {stats['pending']:,}")
    print(f"Manual Review   : {stats['manual_review']:,}")
    print(f"Mappings        : {stats['mappings']:,}")
    print(f"Master Hotels   : {stats['master_hotels']:,}")
    print()

    print(f"Total Processed : {total_processed:,}")
    print(f"Total Time      : {format_time(elapsed)}")

    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())