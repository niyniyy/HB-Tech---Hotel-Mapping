from sqlalchemy import text


class VectorSimilarityService:

    def __init__(self, db):
        self.db = db


    async def find_similar_hotels(
    self,
    embedding: list[float],
    candidate_master_ids: list[int],
    limit: int = 5
):
        """
        Find nearest hotel embeddings using pgvector cosine similarity.
        """

        # Convert Python list to pgvector format
        embedding_str = "[" + ",".join(
            map(str, embedding)
        ) + "]"


        query = text("""
    SELECT
        master_hotel_id,
        1 - (embedding <=> CAST(:embedding AS vector))
            AS similarity_score
    FROM hotel_embeddings
    WHERE master_hotel_id IS NOT NULL
      AND supplier_hotel_id IS NULL
      AND master_hotel_id = ANY(:candidate_master_ids)
    ORDER BY embedding <=> CAST(:embedding AS vector)
    LIMIT :limit
""")


        result = await self.db.execute(
    query,
    {
        "embedding": embedding_str,
        "candidate_master_ids": candidate_master_ids,
        "limit": limit
    }
)


        return result.mappings().all()