"""
Read-only diagnostic: quantify coordinate scatter in supplier_hotels by
geocoding records and measuring how far the supplied coordinate is from where
the provider places the hotel. It NEVER modifies data — it reports the scale of
the problem so you can decide whether coordinate correction is worth wiring into
the pipeline.

A high SUSPECT rate is the direct explanation for duplicate masters: those
records are the ones whose coordinates are too far off to ever become a
candidate for their true hotel.

Usage (from the repo root, in an environment that can reach the database and
the geocoding provider — e.g. inside the api container):

    docker compose exec -e GEOCODING_ENABLED=True api \
        python -m scripts.geocode_validate --limit 100

    # focus one supplier, tighten the threshold:
    docker compose exec -e GEOCODING_ENABLED=True api \
        python -m scripts.geocode_validate --limit 100 --supplier GRN --mismatch-meters 1000

Nominatim (the default provider) is rate-limited to 1 request/second, so
--limit 100 takes ~100 s. Use Google (GEOCODING_PROVIDER=google + key) for
volume.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from app.database.connection import AsyncSessionLocal, engine
from app.matching.geocoding_service import GeocodingService
from config import settings


async def main() -> None:
    parser = argparse.ArgumentParser(description="Measure supplier coordinate scatter via geocoding.")
    parser.add_argument("--limit", type=int, default=100, help="Records to check (1/sec on Nominatim).")
    parser.add_argument("--supplier", type=str, default=None, help="Only this supplier_name.")
    parser.add_argument(
        "--mismatch-meters",
        type=float,
        default=settings.GEOCODING_MISMATCH_METERS,
        help="Supplied coord this far from geocoded = SUSPECT.",
    )
    args = parser.parse_args()

    service = GeocodingService()
    if not service.enabled:
        print(
            "GEOCODING_ENABLED is False. Re-run with the flag on, e.g.\n"
            "  docker compose exec -e GEOCODING_ENABLED=True api "
            "python -m scripts.geocode_validate --limit 100"
        )
        return

    sql = (
        "SELECT id, supplier_name, hotel_name, address, city, state, "
        "postal_code, country, latitude, longitude "
        "FROM supplier_hotels "
        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    )
    params = {"limit": args.limit}
    if args.supplier:
        sql += " AND supplier_name = :supplier"
        params["supplier"] = args.supplier
    sql += " ORDER BY id LIMIT :limit"

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text(sql), params)).mappings().all()

    if not rows:
        print("No supplier_hotels with coordinates matched the filter.")
        await engine.dispose()
        return

    print(
        f"Checking {len(rows)} records via {settings.GEOCODING_PROVIDER}, "
        f"SUSPECT threshold {args.mismatch_meters:.0f} m "
        f"(~{len(rows) * settings.GEOCODING_MIN_INTERVAL_SECONDS:.0f}s on Nominatim)...\n"
    )

    ok = suspect = ungeocodable = 0
    worst: list[tuple] = []

    for row in rows:
        result = await service.validate(
            row["latitude"], row["longitude"],
            name=row["hotel_name"], address=row["address"], city=row["city"],
            state=row["state"], postal_code=row["postal_code"], country=row["country"],
        )
        geo = result["geocoded"]
        mismatch = result["mismatch_meters"]

        if geo is None:
            ungeocodable += 1
        elif mismatch is not None and mismatch > args.mismatch_meters:
            suspect += 1
            worst.append((mismatch, row["hotel_name"], row["city"], row["supplier_name"],
                          float(row["latitude"]), float(row["longitude"]), geo))
        else:
            ok += 1

    scored = len(rows)

    def pct(n):
        return f"{n / scored * 100:.1f}%"

    print(f"Results over {scored} records:")
    print(f"  OK (coordinate agrees)        : {ok:4d}  ({pct(ok)})")
    print(f"  SUSPECT (> {args.mismatch_meters:.0f} m off)      : {suspect:4d}  ({pct(suspect)})"
          "   <- likely bad coordinates -> duplicate-master risk")
    print(f"  ungeocodable (no provider hit): {ungeocodable:4d}  ({pct(ungeocodable)})\n")

    if worst:
        worst.sort(reverse=True)
        print(f"Worst {min(15, len(worst))} mismatches:")
        for mm, name, city, supplier, slat, slng, geo in worst[:15]:
            print(
                f"  {mm/1000:6.1f} km  {name} — {city} [{supplier}]\n"
                f"            supplied ({slat:.4f}, {slng:.4f})  vs  "
                f"geocoded ({geo.latitude:.4f}, {geo.longitude:.4f})"
            )
        print()

    print("Read-only — no coordinates were modified. Calls made:", service.calls)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
