from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any

class MasterHotelService:

    def __init__(self, session: AsyncSession):
        self.session = session
        
    async def get_master_hotel(
    self,
    master_hotel_id: int
) -> dict[str, Any] | None:

        result = await self.session.execute(
            text(
                """
                SELECT
                    master_hotel_id,
                    hotel_name,
                    normalized_name,
                    address,
                    city,
                    state,
                    country,
                    postal_code,
                    latitude,
                    longitude,
                    star_rating,
                    created_at
                FROM master_hotels
                WHERE master_hotel_id = :master_hotel_id;
                """
            ),
            {
                "master_hotel_id": master_hotel_id
            }
        )

        row = result.mappings().first()

        if row is None:
            return None

        return dict(row)
      
    async def get_master_hotel_mappings(
    self,
    master_hotel_id: int
) -> list[dict[str, Any]]:

      result = await self.session.execute(
          text(
              """
              SELECT

                  hm.supplier_name,
                  hm.supplier_hotel_id,
                  hm.mapping_type,
                  hm.match_score,
                  hm.is_manual_verified,

                  sh.hotel_name,
                  sh.address,
                  sh.city,
                  sh.country,
                  sh.star_rating

              FROM hotel_mappings hm

              JOIN supplier_hotels sh
                ON hm.supplier_name = sh.supplier_name
              AND hm.supplier_hotel_id = sh.supplier_hotel_id

              WHERE hm.master_hotel_id = :master_hotel_id

              ORDER BY hm.supplier_name;
              """
          ),
          {
              "master_hotel_id": master_hotel_id
          }
      )

      return [
          dict(row)
          for row in result.mappings().all()
      ]
      
    async def search_master_hotels(
    self,
    query: str,
    limit: int = 50
) -> list[dict[str, Any]]:

        result = await self.session.execute(
            text(
                """
                SELECT
                    master_hotel_id,
                    hotel_name,
                    city,
                    country,
                    star_rating
                FROM master_hotels
                WHERE
                    LOWER(hotel_name) LIKE LOWER(:query)
                    OR LOWER(city) LIKE LOWER(:query)
                    OR LOWER(country) LIKE LOWER(:query)
            ORDER BY hotel_name
            LIMIT :limit;
                """
            ),
            {
                "query": f"%{query}%",
                "limit": limit
            }
        )

        return [
            dict(row)
            for row in result.mappings().all()
        ]
        
    async def get_mapping_statistics(
    self
) -> dict[str, Any]:

        result = await self.session.execute(
            text(
                """
                SELECT

                    (SELECT COUNT(*) FROM supplier_hotels)
                        AS supplier_hotels,

                    (SELECT COUNT(*) FROM master_hotels)
                        AS master_hotels,

                    (SELECT COUNT(*) FROM hotel_mappings)
                        AS hotel_mappings,

                    (
                        SELECT COUNT(*)
                        FROM hotel_mapping_queue
                        WHERE status = 'Pending'
                    ) AS pending,

                    (
                        SELECT COUNT(*)
                        FROM hotel_mapping_queue
                        WHERE status = 'Completed'
                    ) AS completed,

                    (
                        SELECT COUNT(*)
                        FROM hotel_mapping_queue
                        WHERE status = 'ManualReview'
                    ) AS manual_review;
                """
            )
        )

        row = result.mappings().first()

        return dict(row)