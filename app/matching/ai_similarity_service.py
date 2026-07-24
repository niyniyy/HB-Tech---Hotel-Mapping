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
    country: str,
    candidate_master_ids: list[int]
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


        # Step 2: Generate embedding (off the event loop — encode is CPU-bound)
        embedding = await self.embedding_service.generate_embedding_async(
            hotel_text
        )


        # Step 3: Vector search
        matches = await self.vector_service.find_similar_hotels(
    embedding=embedding,
    candidate_master_ids=candidate_master_ids,
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
        "master_hotel_id": match["master_hotel_id"],

        "ai_similarity_score": round(
            similarity * 100,
            2
        ),

        "ai_decision": decision
    }
)
        return results

    async def find_semantic_duplicate_master(
        self,
        supplier_hotel_row_id: int,
        hotel_name: str,
        address: str,
        city: str,
        country: str,
        radius_meters: float,
    ):
        """
        The nearest existing master, by meaning, to a record about to become a
        master of its own.

        The rule engine compares characters inside 1 km. This asks the only
        question that layer cannot: is there already a master somewhere in this
        neighbourhood that *means* the same hotel? Returns the single best
        candidate, or None — the caller decides what to do with it, and the only
        thing it is ever allowed to do is ask a human.
        """
        hotel_text = HotelTextBuilder.build(
            hotel_name,
            address,
            city,
            country
        )

        embedding = await self.embedding_service.generate_embedding_async(
            hotel_text
        )

        matches = await self.vector_service.find_similar_masters_near(
            embedding=embedding,
            supplier_hotel_row_id=supplier_hotel_row_id,
            radius_meters=radius_meters,
            limit=1,
        )

        if not matches:
            return None

        best = matches[0]

        return {
            "master_hotel_id": best["master_hotel_id"],
            "ai_similarity": float(best["similarity_score"]),
        }