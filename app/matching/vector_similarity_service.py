from sqlalchemy import text


class VectorSimilarityService:

    def __init__(self, db):
        self.db = db


    async def find_similar_hotels(
      self,
      embedding: list[float],
      source_supplier_hotel_id: int | None = None,
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
                supplier_hotel_id,
                supplier_name,
                1 - (embedding <=> CAST(:embedding AS vector))
                    AS similarity_score
            FROM hotel_embeddings
            WHERE supplier_hotel_id != :source_supplier_hotel_id
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
""")


        result = await self.db.execute(
            query,
            {
                "embedding": embedding_str,
                "source_supplier_hotel_id": source_supplier_hotel_id,
                "limit": limit
            }
        )


        return result.mappings().all()