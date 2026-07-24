"""
Smoke test for the geocoding service — no database. Geocodes a few known hotels
via Nominatim (keyless) and shows the supplied-vs-geocoded mismatch, so you can
confirm the wiring and see the SUSPECT verdict fire on a deliberately-wrong
coordinate.

Usage (from the repo root):

    GEOCODING_ENABLED=True python -m scripts.geocode_smoke_test

Note: Nominatim's public server is rate-limited to 1 request/second, so this
takes a few seconds. Please don't point bulk traffic at it.
"""

import asyncio

from app.matching.geocoding_service import GeocodingService
from config import settings

# name, address, city, state, postal, country, supplied (lat, lng), note
CASES = [
    (
        "The Taj Mahal Palace", "Apollo Bunder, Colaba", "Mumbai",
        "Maharashtra", "400001", "India",
        (18.9219, 72.8330), "supplied coord ~correct -> expect OK",
    ),
    (
        "ITC Grand Chola", "63 Mount Road, Guindy", "Chennai",
        "Tamil Nadu", "600032", "India",
        (13.0104, 80.2206), "supplied coord ~correct -> expect OK",
    ),
    (
        "The Taj Mahal Palace", "Apollo Bunder, Colaba", "Mumbai",
        "Maharashtra", "400001", "India",
        (19.0760, 72.8777), "supplied coord ~17 km off (Bandra) -> expect SUSPECT",
    ),
]


async def main() -> None:
    service = GeocodingService()
    if not service.enabled:
        print(
            "GEOCODING_ENABLED is False.\n"
            "Run with:  GEOCODING_ENABLED=True python -m scripts.geocode_smoke_test"
        )
        return

    print(f"Provider: {settings.GEOCODING_PROVIDER}  (suspect threshold "
          f"{settings.GEOCODING_MISMATCH_METERS:.0f} m)\n")

    for name, address, city, state, postal, country, supplied, note in CASES:
        result = await service.validate(
            supplied[0], supplied[1],
            name=name, address=address, city=city,
            state=state, postal_code=postal, country=country,
        )
        geo = result["geocoded"]
        print(f"{name} — {city}")
        print(f"  note     : {note}")
        if geo is None:
            print("  UNGEOCODABLE (no result from provider)\n")
            continue
        addr = (geo.matched_address or "")[:70]
        mm = result["mismatch_meters"]
        print(f"  supplied : {supplied[0]:.4f}, {supplied[1]:.4f}")
        print(f"  geocoded : {geo.latitude:.4f}, {geo.longitude:.4f}  ({addr})")
        print(f"  mismatch : {mm:.0f} m" if mm is not None else "  mismatch : n/a")
        print(f"  verdict  : {result['verdict']}\n")

    print(f"Calls made: {service.calls}")


if __name__ == "__main__":
    asyncio.run(main())
