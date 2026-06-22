from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import get_async_session
from normalizer import normalize_hotel_name


router = APIRouter()


@router.post("/hotels/normalize")
async def normalize_hotels(
    limit: int = 100,
    session: AsyncSession = Depends(get_async_session)
):
    """
    Normalize hotel_name into normalized_name for supplier_hotels.
    Processes a limited number of rows at a time.
    """

    result = await session.execute(
        text(
            """
            SELECT id, hotel_name
            FROM supplier_hotels
            WHERE hotel_name IS NOT NULL
              AND (
                    normalized_name IS NULL
                    OR normalized_name = ''
                  )
            ORDER BY id
            LIMIT :limit;
            """
        ),
        {"limit": limit}
    )

    rows = result.mappings().all()

    updated_count = 0

    for row in rows:
        normalized_name = normalize_hotel_name(row["hotel_name"])

        await session.execute(
            text(
                """
                UPDATE supplier_hotels
                SET normalized_name = :normalized_name
                WHERE id = :id;
                """
            ),
            {
                "normalized_name": normalized_name,
                "id": row["id"]
            }
        )

        updated_count += 1

    await session.commit()

    return {
        "message": "Hotel normalization completed",
        "requested_limit": limit,
        "updated_count": updated_count
    }