from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.matching.embedding_service import EmbeddingService
from app.matching.hotel_text_builder import HotelTextBuilder


class MasterEmbeddingService:

    def __init__(self, db: AsyncSession):
        self.db = db
        self.embedding_service = EmbeddingService()

    async def generate_master_embeddings(
    self,
    batch_size: int = 500
):
      total_generated = 0

      while True:

          # Fetch only the next batch of masters
          # that do not already have embeddings
          result = await self.db.execute(
              text(
                  """
                  SELECT
                      m.master_hotel_id,
                      m.hotel_name,
                      m.address,
                      m.city,
                      m.country
                  FROM master_hotels m
                  WHERE NOT EXISTS (
                      SELECT 1
                      FROM hotel_embeddings e
                      WHERE e.master_hotel_id = m.master_hotel_id
                        AND e.supplier_hotel_id IS NULL
                  )
                  ORDER BY m.master_hotel_id
                  LIMIT :batch_size;
                  """
              ),
              {
                  "batch_size": batch_size
              }
          )

          master_hotels = result.mappings().all()

          # No more masters without embeddings
          if not master_hotels:
              break

          # Encode the whole batch in one threaded call rather than one blocking
          # call per hotel.
          hotel_texts = [
              HotelTextBuilder.build(
                  hotel["hotel_name"] or "",
                  hotel["address"] or "",
                  hotel["city"] or "",
                  hotel["country"] or ""
              )
              for hotel in master_hotels
          ]

          embeddings = await self.embedding_service.generate_embeddings_async(
              hotel_texts
          )

          rows = [
              {
                  "master_hotel_id": hotel["master_hotel_id"],
                  "embedding": "[" + ",".join(map(str, embedding)) + "]",
              }
              for hotel, embedding in zip(master_hotels, embeddings)
          ]

          await self.db.execute(
              text(
                  """
                  INSERT INTO hotel_embeddings (
                      master_hotel_id,
                      supplier_hotel_id,
                      supplier_name,
                      embedding
                  )
                  VALUES (
                      :master_hotel_id,
                      NULL,
                      NULL,
                      CAST(:embedding AS vector)
                  );
                  """
              ),
              rows
          )

          total_generated += len(rows)

          # Save progress after every batch
          await self.db.commit()

          print(
              f"Master embedding batch completed. "
              f"Total generated: {total_generated}"
          )

      return {
          "generated_embeddings": total_generated
      }
        
    async def generate_embedding_for_master(
    self,
    master_hotel_id: int
) -> bool:

      result = await self.db.execute(
          text(
              """
              SELECT
                  master_hotel_id,
                  hotel_name,
                  address,
                  city,
                  country
              FROM master_hotels
              WHERE master_hotel_id = :master_hotel_id;
              """
          ),
          {
              "master_hotel_id": master_hotel_id
          }
      )

      hotel = result.mappings().first()

      if hotel is None:
          return False

      # Avoid duplicate embedding
      existing = await self.db.execute(
          text(
              """
              SELECT 1
              FROM hotel_embeddings
              WHERE master_hotel_id = :master_hotel_id
                AND supplier_hotel_id IS NULL;
              """
          ),
          {
              "master_hotel_id": master_hotel_id
          }
      )

      if existing.scalar() is not None:
          return True

      hotel_text = HotelTextBuilder.build(
          hotel["hotel_name"] or "",
          hotel["address"] or "",
          hotel["city"] or "",
          hotel["country"] or ""
      )

      embedding = await self.embedding_service.generate_embedding_async(
          hotel_text
      )

      embedding_str = "[" + ",".join(
          map(str, embedding)
      ) + "]"

      await self.db.execute(
          text(
              """
              INSERT INTO hotel_embeddings (
                  master_hotel_id,
                  supplier_hotel_id,
                  supplier_name,
                  embedding
              )
              VALUES (
                  :master_hotel_id,
                  NULL,
                  NULL,
                  CAST(:embedding AS vector)
              );
              """
          ),
          {
              "master_hotel_id": master_hotel_id,
              "embedding": embedding_str
          }
      )

      return True