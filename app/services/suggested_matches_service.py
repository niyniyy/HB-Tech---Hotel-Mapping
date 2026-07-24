from app.matching.ai_similarity_service import AISimilarityService
from app.repositories.hotel_repository import SupplierHotelRepository


class SuggestedMatchesService:

    def __init__(self, db):
        self.db = db

        self.ai_service = AISimilarityService(db)

        self.hotel_repo = SupplierHotelRepository(db)


    async def get_suggestions(
        self,
        supplier_hotel_id: int
    ):

        hotel = await self.hotel_repo.get_by_id(
            supplier_hotel_id
        )

        if not hotel:
            return []

        return await self.ai_service.find_ai_matches(
            supplier_hotel_id=hotel.id,
            hotel_name=hotel.hotel_name,
            address=hotel.address,
            city=hotel.city,
            country=hotel.country
        )
        