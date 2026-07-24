"""
Geocode correction — validate supplier coordinates and correct the suspect ones.

For each supplier record it geocodes name+address and measures the supplied
coordinate against the result:
  * OK           supplied coordinate agrees          -> nothing to fix
  * SUSPECT      supplied coordinate is >threshold off -> write the geocoded
                 coordinate into match_geo_location, which candidate search
                 uses instead (so the record stops fragmenting into its own
                 master), and mark geocode_status='SUSPECT'
  * UNGEOCODABLE provider returned nothing            -> recorded, left as-is

Raw latitude/longitude are NEVER modified — the original supplied coordinate
stays recoverable in the raw columns; the correction lives only in
match_geo_location.

"Flag" here means the geocode_status='SUSPECT' marker (queryable for
supplier-quality reporting), NOT flagged_records — that table excludes a record
from mapping entirely, which would defeat correcting the coordinate so it maps.

Prerequisite: scripts/migration_geocode_correction.sql must have run.

Usage (inside the api container, from repo root):
    docker compose exec -e GEOCODING_ENABLED=True api \
        python -m scripts.geocode_correct --limit 100            # apply
    ...  --dry-run                                               # preview only
    ...  --supplier Booking.com                                  # one supplier
    ...  --recheck                                               # re-check checked rows

After applying, re-run the pipeline for the corrected coordinates to take effect
in candidate search.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from app.database.connection import AsyncSessionLocal, engine
from app.matching.geocoding_service import GeocodingService
from config import settings

_UPDATE = text(
    """
    UPDATE supplier_hotels SET
        geocoded_latitude       = :glat,
        geocoded_longitude      = :glng,
        geocode_status          = :status,
        geocode_mismatch_meters = :mismatch,
        geocode_checked_at      = NOW(),
        -- Correct only the suspect ones; OK/ungeocodable clear any prior
        -- correction so a re-check can't leave a stale one behind.
        match_geo_location      = CASE
            WHEN :status = 'SUSPECT' AND :glat IS NOT NULL
            THEN ST_SetSRID(ST_MakePoint(:glng, :glat), 4326)::geography
            ELSE NULL
        END
    WHERE id = :id
    """
)

_COMMIT_EVERY = 25


async def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and correct supplier coordinates.")
    parser.add_argument("--limit", type=int, default=100, help="Max records to process.")
    parser.add_argument("--supplier", type=str, default=None, help="Only this supplier_name.")
    parser.add_argument("--recheck", action="store_true", help="Re-check already-checked rows too.")
    parser.add_argument("--dry-run", action="store_true", help="Preview; write nothing.")
    args = parser.parse_args()

    service = GeocodingService()
    if not service.enabled:
        print(
            "GEOCODING_ENABLED is False. Re-run with the flag on, e.g.\n"
            "  docker compose exec -e GEOCODING_ENABLED=True api "
            "python -m scripts.geocode_correct --limit 100"
        )
        return

    sql = (
        "SELECT id, supplier_name, hotel_name, address, city, state, "
        "postal_code, country, latitude, longitude "
        "FROM supplier_hotels "
        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    )
    params = {"limit": args.limit}
    if not args.recheck:
        sql += " AND geocode_status IS NULL"
    if args.supplier:
        sql += " AND supplier_name = :supplier"
        params["supplier"] = args.supplier
    sql += " ORDER BY id LIMIT :limit"

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text(sql), params)).mappings().all()

        if not rows:
            print("No records to process (all checked already? try --recheck).")
            await engine.dispose()
            return

        mode = "DRY RUN (no writes)" if args.dry_run else "APPLYING"
        print(
            f"{mode}: {len(rows)} records via {settings.GEOCODING_PROVIDER}, "
            f"SUSPECT threshold {settings.GEOCODING_MISMATCH_METERS:.0f} m\n"
        )

        ok = suspect = ungeocodable = corrected = 0

        for index, row in enumerate(rows, start=1):
            result = await service.validate(
                row["latitude"], row["longitude"],
                name=row["hotel_name"], address=row["address"], city=row["city"],
                state=row["state"], postal_code=row["postal_code"], country=row["country"],
            )
            geo = result["geocoded"]
            status = result["verdict"]  # OK | SUSPECT | UNGEOCODABLE

            if status == "OK":
                ok += 1
            elif status == "SUSPECT":
                suspect += 1
            else:
                ungeocodable += 1

            if not args.dry_run:
                await session.execute(
                    _UPDATE,
                    {
                        "id": row["id"],
                        "glat": geo.latitude if geo else None,
                        "glng": geo.longitude if geo else None,
                        "status": status,
                        "mismatch": result["mismatch_meters"],
                    },
                )
                if status == "SUSPECT" and geo is not None:
                    corrected += 1
                if index % _COMMIT_EVERY == 0:
                    await session.commit()

        if not args.dry_run:
            await session.commit()

    scored = len(rows)

    def pct(n):
        return f"{n / scored * 100:.1f}%"

    print(f"Processed {scored} records:")
    print(f"  OK          : {ok:4d}  ({pct(ok)})")
    print(f"  SUSPECT     : {suspect:4d}  ({pct(suspect)})")
    print(f"  ungeocodable: {ungeocodable:4d}  ({pct(ungeocodable)})")
    if args.dry_run:
        print(f"\nDry run — no changes written. {suspect} record(s) WOULD be corrected.")
    else:
        print(f"\nWrote corrections for {corrected} suspect record(s) (match_geo_location set).")
        print("Re-run the pipeline for the corrected coordinates to take effect.")

    print("Calls made:", service.calls)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
