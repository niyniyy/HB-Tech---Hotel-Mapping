from sqlalchemy import select

from app.models.hotel import SupplierHotel, HotelEmbedding
from app.matching.hotel_text_builder import HotelTextBuilder
from app.matching.embedding_service import EmbeddingService


class EmbeddingPopulationService:

    def __init__(self, db):
        self.db = db
        self.embedding_service = EmbeddingService()

    async def populate_embeddings(self, limit: int = 10) -> int:
        """
        Generate embeddings for supplier hotels.
        """

        result = await self.db.execute(
          select(SupplierHotel)
          .where(
              ~SupplierHotel.id.in_(
                  select(HotelEmbedding.supplier_hotel_id)
              )
          )
          .limit(limit)
      )

        hotels = result.scalars().all()

        inserted = 0

        for hotel in hotels:

            text = HotelTextBuilder.build(
                hotel.hotel_name,
                hotel.address,
                hotel.city,
                hotel.country,
            )

            embedding = self.embedding_service.generate_embedding(text)

            row = HotelEmbedding(
                supplier_hotel_id=hotel.id,
                supplier_name=hotel.supplier_name,
                embedding=embedding,
            )

            self.db.add(row)

            inserted += 1

        await self.db.flush()

        return inserted