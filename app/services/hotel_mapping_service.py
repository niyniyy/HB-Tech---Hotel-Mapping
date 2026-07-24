from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any

from app.normalization.normalizer import core_hotel_name, normalize_hotel_name

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
    is_manual_verified: bool = False,
    evidence: dict[str, Any] | None = None
) -> None:
        """
        Every mapping records the evidence that produced it — name similarity,
        distance and confidence tier. A false merge routes a guest to a property
        they did not book, so each decision must be auditable and reversible
        after the fact, not merely rare.
        """
        evidence = evidence or {}

        await self.session.execute(
            text(
                """
                INSERT INTO hotel_mappings (
                    master_hotel_id,
                    supplier_hotel_row_id,
                    supplier_name,
                    supplier_hotel_id,
                    match_score,
                    mapping_type,
                    is_manual_verified,
                    name_similarity,
                    name_similarity_strict,
                    distance_meters,
                    confidence_tier
                )
                VALUES (
                    :master_hotel_id,
                    :supplier_hotel_row_id,
                    :supplier_name,
                    :supplier_hotel_id,
                    :match_score,
                    :mapping_type,
                    :is_manual_verified,
                    :name_similarity,
                    :name_similarity_strict,
                    :distance_meters,
                    :confidence_tier
                );
                """
            ),
            {
                "master_hotel_id": master_hotel_id,
                # The real row identity. (supplier_name, supplier_hotel_id) is
                # NOT unique in supplier data — Sabre reuses 89 ids across 212
                # rows for different hotels — so a mapping keyed on it is
                # ambiguous and cannot be resolved back to one property.
                "supplier_hotel_row_id": supplier_hotel.get("id"),
                "supplier_name": supplier_hotel.get("supplier_name"),
                "supplier_hotel_id": supplier_hotel.get("supplier_hotel_id"),
                "match_score": match_score,
                "mapping_type": mapping_type,
                "is_manual_verified": is_manual_verified,
                "name_similarity": evidence.get("name_similarity"),
                "name_similarity_strict": evidence.get("name_similarity_strict"),
                "distance_meters": evidence.get("distance_meters"),
                "confidence_tier": evidence.get("confidence_tier", "TIER1"),
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
                    core_name,
                    strict_name,
                    address,
                    city,
                    state,
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
                    :core_name,
                    :strict_name,
                    :address,
                    :city,
                    :state,
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
                # Recomputed rather than copied: the supplier row's stored value
                # may predate the column, and the blocking key must never be
                # silently NULL — a master with no core_name is invisible to the
                # exact-name pass and will fragment.
                "core_name": (
                    supplier_hotel.get("core_name")
                    or core_hotel_name(
                        supplier_hotel.get("hotel_name"),
                        supplier_hotel.get("city"),
                        supplier_hotel.get("state"),
                    )
                    or None
                ),
                "strict_name": (
                    supplier_hotel.get("strict_name")
                    or normalize_hotel_name(supplier_hotel.get("hotel_name"))
                    or None
                ),
                "address": supplier_hotel.get("address"),
                "city": supplier_hotel.get("city"),
                # state was silently dropped here, so every master had a NULL
                # state while its supplier rows had one — which also meant
                # core_hotel_name could not strip the state from master names.
                "state": supplier_hotel.get("state"),
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