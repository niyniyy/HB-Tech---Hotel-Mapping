from app.matching.embedding_service import EmbeddingService
from app.matching.hotel_text_builder import HotelTextBuilder
from app.matching.vector_similarity_service import VectorSimilarityService


class AISimilarityService:

    def __init__(self, db):
        self.db = db

        self.embedding_service = EmbeddingService()

        self.vector_service = VectorSimilarityService(db)


    async def find_ai_matches(
      self,
      supplier_hotel_id: int,
      hotel_name: str,
      address: str,
      city: str,
      country: str
):
        """
        Generate AI similarity matches for a hotel.
        """

        # Step 1: Build input text
        hotel_text = HotelTextBuilder.build(
            hotel_name,
            address,
            city,
            country
        )


        # Step 2: Generate embedding
        embedding = self.embedding_service.generate_embedding(
            hotel_text
        )


        # Step 3: Vector search
        matches = await self.vector_service.find_similar_hotels(
          embedding,
          source_supplier_hotel_id=supplier_hotel_id,
          limit=5
        )


        results = []

        for match in matches:

            similarity = float(
                match["similarity_score"]
            )

            if similarity >= 0.85:
                decision = "AI_MATCH"

            elif similarity >= 0.70:
                decision = "AI_SUGGESTED"

            else:
                decision = "LOW_CONFIDENCE"


            results.append(
              {
                  "candidate_supplier_hotel_id": match["supplier_hotel_id"],
                  "candidate_supplier_name": match["supplier_name"],

                  "ai_similarity_score": round(
                      similarity * 100,
                      2
                  ),

                  "ai_decision": decision
              }
)


        return results