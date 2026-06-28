class HotelTextBuilder:

    @staticmethod
    def build(
        hotel_name: str | None,
        address: str | None,
        city: str | None,
        country: str | None,
    ) -> str:

        parts = [
            hotel_name,
            address,
            city,
            country,
        ]

        return " ".join(
            str(p).strip()
            for p in parts
            if p and str(p).strip()
        )