from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any

class HotelMappingService:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def update_queue_status(
        self,
        supplier_hotel_record_id: int,
        status: str
    ):
        await self.session.execute(
            text(
                """
                UPDATE hotel_mapping_queue
                SET status = :status
                WHERE supplier_hotel_id = :supplier_hotel_id;
                """
            ),
            {
                "status": status,
                "supplier_hotel_id": supplier_hotel_record_id
            }
        )
        
    async def insert_hotel_mapping(
    self,
    master_hotel_id: int,
    supplier_hotel: dict[str, Any],
    match_score: float,
    mapping_type: str,
    is_manual_verified: bool = False
) -> None:
        await self.session.execute(
            text(
                """
                INSERT INTO hotel_mappings (
                    master_hotel_id,
                    supplier_name,
                    supplier_hotel_id,
                    match_score,
                    mapping_type,
                    is_manual_verified
                )
                VALUES (
                    :master_hotel_id,
                    :supplier_name,
                    :supplier_hotel_id,
                    :match_score,
                    :mapping_type,
                    :is_manual_verified
                );
                """
            ),
            {
                "master_hotel_id": master_hotel_id,
                "supplier_name": supplier_hotel.get("supplier_name"),
                "supplier_hotel_id": supplier_hotel.get("supplier_hotel_id"),
                "match_score": match_score,
                "mapping_type": mapping_type,
                "is_manual_verified": is_manual_verified
            }
        )
        
    async def create_master_hotel_from_supplier(
    self,
    supplier_hotel: dict[str, Any]
) -> int:
       
        result = await self.session.execute(
            text(
                """
                INSERT INTO master_hotels (
                    hotel_name,
                    normalized_name,
                    address,
                    city,
                    country,
                    postal_code,
                    star_rating,
                    latitude,
                    longitude,
                    geo_location
                )
                VALUES (
                    :hotel_name,
                    :normalized_name,
                    :address,
                    :city,
                    :country,
                    :postal_code,
                    :star_rating,
                    :latitude,
                    :longitude,
                    :geo_location
                )
                RETURNING master_hotel_id;
                """
            ),
            {
                "hotel_name": supplier_hotel.get("hotel_name"),
                "normalized_name": supplier_hotel.get("normalized_name"),
                "address": supplier_hotel.get("address"),
                "city": supplier_hotel.get("city"),
                "country": supplier_hotel.get("country"),
                "postal_code": supplier_hotel.get("postal_code"),
                "star_rating": supplier_hotel.get("star_rating"),
                "latitude": supplier_hotel.get("latitude"),
                "longitude": supplier_hotel.get("longitude"),
                "geo_location": supplier_hotel.get("geo_location"),
            }
        )

        return result.scalar_one()
      
    async def delete_manual_review_candidate(
        self,
        supplier_hotel_record_id: int
    ):
        await self.session.execute(
            text(
                """
                DELETE FROM manual_review_candidates
                WHERE supplier_hotel_id = :supplier_hotel_id;
                """
            ),
            {
                "supplier_hotel_id": supplier_hotel_record_id
            }
        )