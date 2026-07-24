"""
Populate core_name and strict_name on supplier_hotels and master_hotels.

Both are Python-computed — they strip marketing boilerplate, stop words and
(for core_name) the city/state tokens — so the columns cannot be filled by
SQL. Run once after applying
scripts/migration_provisional_and_core_name.sql; both import paths populate
them going forward.

Idempotent: recomputes every row, so it is also the way to rebuild the keys
after a change to the normalizer's stop words or marketing patterns.

    python -m scripts.backfill_core_name
"""

import asyncio
import logging

from sqlalchemy import text

from app.database.connection import AsyncSessionLocal
from app.normalization.normalizer import core_hotel_name, normalize_hotel_name

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

BATCH_SIZE = 2000

TABLES = (
    ("supplier_hotels", "id"),
    ("master_hotels", "master_hotel_id"),
)


async def backfill_table(session, table: str, key: str) -> int:
    updated = 0
    last_key = 0

    while True:
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT {key} AS row_key, hotel_name, city, state
                    FROM {table}
                    WHERE {key} > :last_key
                    ORDER BY {key}
                    LIMIT :limit;
                    """
                ),
                {"last_key": last_key, "limit": BATCH_SIZE},
            )
        ).mappings().all()

        if not rows:
            break

        keys = []
        cores = []
        stricts = []

        for row in rows:
            keys.append(row["row_key"])
            cores.append(
                core_hotel_name(row["hotel_name"], row["city"], row["state"]) or None
            )
            stricts.append(normalize_hotel_name(row["hotel_name"]) or None)

        await session.execute(
            text(
                f"""
                UPDATE {table} AS t
                SET core_name   = u.core_name,
                    strict_name = u.strict_name
                FROM unnest(
                    CAST(:keys AS bigint[]),
                    CAST(:cores AS text[]),
                    CAST(:stricts AS text[])
                ) AS u(row_key, core_name, strict_name)
                WHERE t.{key} = u.row_key
                  AND (t.core_name   IS DISTINCT FROM u.core_name
                    OR t.strict_name IS DISTINCT FROM u.strict_name);
                """
            ),
            {"keys": keys, "cores": cores, "stricts": stricts},
        )

        await session.commit()

        updated += len(rows)
        last_key = keys[-1]

        logger.info("  %s: %d rows processed", table, updated)

    return updated


async def main():
    async with AsyncSessionLocal() as session:
        for table, key in TABLES:
            logger.info("Backfilling core_name / strict_name on %s", table)
            count = await backfill_table(session, table, key)
            logger.info("  done — %d rows", count)

        summary = (
            await session.execute(
                text(
                    """
                    SELECT 'supplier_hotels' AS table_name,
                           count(*) AS rows,
                           count(*) FILTER (WHERE core_name IS NULL) AS empty_core
                    FROM supplier_hotels
                    UNION ALL
                    SELECT 'master_hotels', count(*),
                           count(*) FILTER (WHERE core_name IS NULL)
                    FROM master_hotels;
                    """
                )
            )
        ).mappings().all()

        for row in summary:
            logger.info(
                "%s: %d rows, %d without a usable core name",
                row["table_name"], row["rows"], row["empty_core"],
            )


if __name__ == "__main__":
    asyncio.run(main())
