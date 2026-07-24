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

    async def find_similar_masters_near(
        self,
        embedding: list[float],
        supplier_hotel_row_id: int,
        radius_meters: float,
        limit: int = 5,
    ):
        """
        Nearest master embeddings among masters within `radius_meters` of this
        supplier record — the search the rule engine could not perform.

        Scoped by geography, never by city string. Joining on `city` is what
        produced 607 duplicate masters (Bangalore/Bengaluru, Gurgaon/Gurugram,
        Madikeri/Kodagu) and was deliberately removed from candidate retrieval;
        rebuilding it here would put the same fault inside the layer meant to
        catch its consequences.

        The radius is wider than the matcher's because that is the entire point:
        this runs only when the 1 km search already came back empty-handed.
        """
        embedding_str = "[" + ",".join(
            map(str, embedding)
        ) + "]"

        query = text("""
    WITH nearby AS (
        SELECT m.master_hotel_id
        FROM supplier_hotels s
        JOIN master_hotels m
          ON ST_DWithin(m.geo_location, s.geo_location, :radius)
        WHERE s.id = :row_id
          AND s.geo_location IS NOT NULL
    )
    SELECT e.master_hotel_id,
           1 - (e.embedding <=> CAST(:embedding AS vector))
               AS similarity_score
    FROM hotel_embeddings e
    JOIN nearby n ON n.master_hotel_id = e.master_hotel_id
    WHERE e.supplier_hotel_id IS NULL
    ORDER BY e.embedding <=> CAST(:embedding AS vector)
    LIMIT :limit
""")

        result = await self.db.execute(
            query,
            {
                "embedding": embedding_str,
                "row_id": supplier_hotel_row_id,
                "radius": radius_meters,
                "limit": limit,
            }
        )

        return result.mappings().all()