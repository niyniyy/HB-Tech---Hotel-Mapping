from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.hotel_mapping_service import HotelMappingService
from sqlalchemy.exc import IntegrityError
from typing import Any
from app.matching.master_embedding_service import MasterEmbeddingService

class ManualReviewService:

    def __init__(self, session: AsyncSession):
        self.session = session
        self.mapping_service = HotelMappingService(session)
        self.master_embedding_service = MasterEmbeddingService(session)
    async def get_manual_reviews(
        self,
        limit: int = 100
) -> list[dict[str, Any]]:
        result = await self.session.execute(
    text(
        """
        SELECT
    q.supplier_hotel_id,

    s.supplier_name,
    s.supplier_hotel_id AS supplier_hotel_code,
    s.hotel_name,
    s.normalized_name,
    s.address,
    s.city,
    s.state,
    s.country,
    s.postal_code,
    s.star_rating,
    s.latitude,
    s.longitude,

    c.suggested_master_hotel_id,

    m.hotel_name AS master_hotel_name,
    m.normalized_name AS master_normalized_name,
    m.address AS master_address,
    m.city AS master_city,
    m.state AS master_state,
    m.country AS master_country,
    m.postal_code AS master_postal_code,
    m.star_rating AS master_star_rating,
    m.latitude AS master_latitude,
    m.longitude AS master_longitude,

    c.rule_score,
    c.ai_similarity,
    c.decision_reason,
    c.created_at

        FROM hotel_mapping_queue q

        JOIN manual_review_candidates c
          ON q.supplier_hotel_id = c.supplier_hotel_id

        JOIN supplier_hotels s
          ON s.id = q.supplier_hotel_id

        JOIN master_hotels m
          ON m.master_hotel_id = c.suggested_master_hotel_id

        WHERE q.status = 'ManualReview'

        ORDER BY c.created_at DESC

        LIMIT :limit;
        """
    ),
    {"limit": limit}
)

        return [dict(row) for row in result.mappings().all()]
      
    async def get_manual_review(
        self,
        supplier_hotel_id: int
    ) -> dict[str, Any] | None:

        result = await self.session.execute(
            text(
                """
                SELECT
                    q.supplier_hotel_id,

                    s.supplier_name,
                    s.supplier_hotel_id AS supplier_hotel_code,
                    s.hotel_name,
                    s.normalized_name,
                    s.address,
                    s.city,
                    s.state,
                    s.country,
                    s.postal_code,
                    s.star_rating,
                    s.latitude,
                    s.longitude,

                    c.suggested_master_hotel_id,

                    m.hotel_name AS master_hotel_name,
                    m.normalized_name AS master_normalized_name,
                    m.address AS master_address,
                    m.city AS master_city,
                    m.state AS master_state,
                    m.country AS master_country,
                    m.postal_code AS master_postal_code,
                    m.star_rating AS master_star_rating,
                    m.latitude AS master_latitude,
                    m.longitude AS master_longitude,

                    c.rule_score,
                    c.ai_similarity,
                    c.decision_reason,
                    c.created_at

                FROM hotel_mapping_queue q

                JOIN manual_review_candidates c
                    ON q.supplier_hotel_id = c.supplier_hotel_id

                JOIN supplier_hotels s
                    ON s.id = q.supplier_hotel_id

                JOIN master_hotels m
                    ON m.master_hotel_id = c.suggested_master_hotel_id

                WHERE
                    q.status = 'ManualReview'
                    AND q.supplier_hotel_id = :supplier_hotel_id;
                """
            ),
            {
                "supplier_hotel_id": supplier_hotel_id
            }
        )

        row = result.mappings().first()

        if row is None:
            return None

        return dict(row)
      
    async def get_review_record(
    self,
    supplier_hotel_id: int
) -> dict[str, Any] | None:

        result = await self.session.execute(
            text(
                """
                SELECT

                    c.suggested_master_hotel_id,
                    c.rule_score,
                    
                    s.supplier_name,
                    s.supplier_hotel_id

                FROM manual_review_candidates c

                JOIN supplier_hotels s
                  ON s.id = c.supplier_hotel_id

                WHERE c.supplier_hotel_id = :supplier_hotel_id;
                """
            ),
            {
                "supplier_hotel_id": supplier_hotel_id
            }
        )

        row = result.mappings().first()

        if row is None:
            return None

        return dict(row)
      
    async def get_supplier_hotel(
    self,
    supplier_hotel_id: int
) -> dict[str, Any] | None:
        
        result = await self.session.execute(
            text(
                """
                SELECT *
                FROM supplier_hotels
                WHERE id = :supplier_hotel_id;
                """
            ),
            {
                "supplier_hotel_id": supplier_hotel_id
            }
        )

        row = result.mappings().first()

        if row is None:
            return None

        return dict(row)
    
    async def mapping_exists(
    self,
    supplier_name: str,
    supplier_hotel_id: str
) -> bool:
        result = await self.session.execute(
            text(
                """
                SELECT 1
                FROM hotel_mappings
                WHERE supplier_name = :supplier_name
                  AND supplier_hotel_id = :supplier_hotel_id;
                """
            ),
            {
                "supplier_name": supplier_name,
                "supplier_hotel_id": supplier_hotel_id
            }
        )

        return result.scalar() is not None 
      
    async def approve_match(
    self,
    supplier_hotel_id: int
) -> bool:

      review = await self.get_review_record(
          supplier_hotel_id
      )
      
      
      if review is None:
          return False
        
  
      already_exists = await self.mapping_exists(
          review["supplier_name"],
          review["supplier_hotel_id"]
        )
      if already_exists:
          return False

      try:

        await self.mapping_service.insert_hotel_mapping(
              master_hotel_id=review["suggested_master_hotel_id"],
              supplier_hotel=review,
              match_score=review["rule_score"],
              mapping_type="MANUAL",
              is_manual_verified=True
          )
        
        await self.mapping_service.update_queue_status(
          supplier_hotel_id,
          "Completed"
      )
        
        await self.mapping_service.delete_manual_review_candidate(
          supplier_hotel_id
      )

        await self.session.commit()
        
        return True

      except IntegrityError:

        await self.session.rollback()

        return False
      
    async def create_new_master(
    self,
    supplier_hotel_id: int
) -> bool:

        review = await self.get_review_record(
            supplier_hotel_id
        )

        if review is None:
            return False

        supplier_hotel = await self.get_supplier_hotel(
            supplier_hotel_id
        )

        if supplier_hotel is None:
            return False

        try:

            new_master_hotel_id = await self.mapping_service.create_master_hotel_from_supplier(
                supplier_hotel
            )
            # Generate embedding for the newly created master
            await self.master_embedding_service.generate_embedding_for_master(
                new_master_hotel_id
            )
            await self.mapping_service.insert_hotel_mapping(
                master_hotel_id=new_master_hotel_id,
                supplier_hotel=supplier_hotel,
                match_score=100.0,
                mapping_type="NEW_MASTER",
                is_manual_verified=True
            )

            await self.mapping_service.update_queue_status(
                supplier_hotel_id,
                "Completed"
            )

            await self.mapping_service.delete_manual_review_candidate(
                supplier_hotel_id
            )

            await self.session.commit()

            return True

        except Exception as error:

            await self.session.rollback()

            print(
                f"Error creating new master hotel "
                f"for supplier hotel {supplier_hotel_id}: {error}"
            )

            return False
          
          
    async def reject_review(
    self,
    supplier_hotel_id: int
) -> bool:
        review = await self.get_review_record(
            supplier_hotel_id
        )

        if review is None:
            return False

        try:

            await self.mapping_service.delete_manual_review_candidate(
                supplier_hotel_id
            )

            await self.mapping_service.update_queue_status(
                supplier_hotel_id,
                "Pending"
            )

            await self.session.commit()

            return True

        except Exception:

            await self.session.rollback()

            return False