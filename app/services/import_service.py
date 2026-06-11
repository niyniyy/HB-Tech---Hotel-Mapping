import pandas as pd
import math
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.hotel_repository import SupplierHotelRepository, MappingQueueRepository
from app.models.schemas import ImportSummary

logger = logging.getLogger(__name__)

BATCH_SIZE = 1000

# Map each supplier's CSV columns to the supplier_hotels schema
COLUMN_MAP = {
    "supplier_name": "supplier_name",
    "supplier_hotel_id": "supplier_hotel_id",
    "hotel_name": "hotel_name",
    "normalized_name": "normalized_name",
    "address": "address",
    "city": "city",
    "state": "state",
    "country": "country",
    "postal_code": "postal_code",
    "latitude": "latitude",
    "longitude": "longitude",
    "star_rating": "star_rating",
}


class ImportService:
    """
    Handles importing supplier CSV files into the supplier_hotels table.
    Processes in batches of 1000 rows to avoid memory issues.
    After import, auto-populates hotel_mapping_queue.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.hotel_repo = SupplierHotelRepository(db)
        self.queue_repo = MappingQueueRepository(db)

    async def import_from_csv(self, file_path: str, supplier_name: str) -> ImportSummary:
        """
        Main import method.
        Reads CSV, validates rows, inserts in batches, enqueues for mapping.
        """
        logger.info(f"Starting import for supplier: {supplier_name} from {file_path}")

        try:
            df = pd.read_csv(file_path)
        except Exception as e:
            logger.error(f"Failed to read CSV: {e}")
            raise ValueError(f"Could not read file: {e}")

        total_rows = len(df)
        inserted = 0
        skipped = 0
        errors = 0
        inserted_ids = []

        # Process in batches
        num_batches = math.ceil(total_rows / BATCH_SIZE)
        logger.info(f"Processing {total_rows} rows in {num_batches} batches")

        for batch_num in range(num_batches):
            start = batch_num * BATCH_SIZE
            end = start + BATCH_SIZE
            batch_df = df.iloc[start:end]

            batch_data = []
            for _, row in batch_df.iterrows():
                cleaned = self._clean_row(row, supplier_name)
                if cleaned is None:
                    skipped += 1
                    continue
                batch_data.append(cleaned)

            if batch_data:
                try:
                    count = await self.hotel_repo.bulk_insert(batch_data)
                    inserted += count
                    logger.info(f"Batch {batch_num + 1}/{num_batches}: inserted {count} rows")
                except Exception as e:
                    logger.error(f"Batch {batch_num + 1} failed: {e}")
                    errors += len(batch_data)

        # Enqueue all inserted hotels for mapping
        if inserted_ids:
            await self.queue_repo.bulk_enqueue(inserted_ids)

        logger.info(f"Import complete: {inserted} inserted, {skipped} skipped, {errors} errors")

        return ImportSummary(
            supplier_name=supplier_name,
            total_rows=total_rows,
            inserted=inserted,
            skipped=skipped,
            errors=errors,
            message=f"Import complete for {supplier_name}"
        )

    def _clean_row(self, row: pd.Series, supplier_name: str) -> dict | None:
        """
        Validate and clean a single row before insert.
        Returns None if row should be skipped.
        """
        # Must have supplier_hotel_id
        if pd.isnull(row.get("supplier_hotel_id")):
            return None

        # Must have country (needed for Stage 1 matching)
        if pd.isnull(row.get("country")):
            logger.warning(f"Skipping row with null country: {row.get('supplier_hotel_id')}")
            return None

        # Parse lat/lon — skip geo_location if null (Celery will handle)
        try:
            lat = float(row["latitude"]) if not pd.isnull(row.get("latitude")) else None
            lon = float(row["longitude"]) if not pd.isnull(row.get("longitude")) else None
        except (ValueError, TypeError):
            lat = None
            lon = None

        # Parse star_rating
        try:
            star = float(row["star_rating"]) if not pd.isnull(row.get("star_rating")) else None
            if star is not None and (star < 0 or star > 5):
                star = None
        except (ValueError, TypeError):
            star = None

        return {
            "supplier_name": supplier_name,
            "supplier_hotel_id": str(row.get("supplier_hotel_id", "")).strip(),
            "hotel_name": str(row["hotel_name"]).strip() if not pd.isnull(row.get("hotel_name")) else None,
            "normalized_name": str(row["normalized_name"]).strip() if not pd.isnull(row.get("normalized_name")) else None,
            "address": str(row["address"]).strip() if not pd.isnull(row.get("address")) else None,
            "city": str(row["city"]).strip() if not pd.isnull(row.get("city")) else None,
            "state": str(row["state"]).strip() if not pd.isnull(row.get("state")) else None,
            "country": str(row["country"]).strip() if not pd.isnull(row.get("country")) else None,
            "postal_code": str(row["postal_code"]).strip() if not pd.isnull(row.get("postal_code")) else None,
            "latitude": lat,
            "longitude": lon,
            "star_rating": star,
            "raw_json": row.to_dict(),
        }
