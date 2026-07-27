import io
import logging
import math
import re

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.normalization.normalizer import core_hotel_name, normalize_hotel_name
from app.services.import_staging import stage_upload

logger = logging.getLogger(__name__)

BATCH_SIZE = 1000

# Supplier files never agree on column names. Rather than demanding one fixed
# schema — which silently produced NULL hotel names when a header did not match —
# headers are matched against these synonyms and the detected mapping is shown
# to the user for confirmation before anything is written.
COLUMN_SYNONYMS = {
    "supplier_hotel_id": [
        "supplier_hotel_id", "supplierid", "supplier_id", "hotelid", "hotel_id",
        "propertyid", "property_id", "code", "hotelcode", "id",
    ],
    "hotel_name": [
        "hotel_name", "hotelname", "mapping_hotel_name", "name", "propertyname",
        "property_name", "title",
    ],
    "normalized_name": ["normalized_hotelname", "normalized_name", "normalised_name"],
    "address": [
        "address", "mapping_address", "street", "address1", "address_line_1",
        "streetaddress", "full_address",
    ],
    "city": ["city", "cityname", "city_name", "town"],
    "state": ["state", "statename", "state_name", "province", "region"],
    "country": ["country", "countryname", "country_name", "countrycode"],
    "postal_code": [
        "postal_code", "postalcode", "postal", "pincode", "pin", "zip",
        "zipcode", "zip_code",
    ],
    "latitude": ["latitude", "lat"],
    "longitude": ["longitude", "longitude_", "lon", "lng", "long"],
    "latlon": ["latitudelongitude", "latlong", "lat_long", "coordinates", "geo", "latlng"],
    "star_rating": ["star_rating", "starrating", "stars", "star", "rating", "category"],
    "supplier_name": ["supplier_name", "supplier", "source", "provider"],
}

REQUIRED = ["supplier_hotel_id", "hotel_name", "country"]


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def detect_columns(headers: list[str]) -> dict:
    """
    Map source headers onto our fields. Exact synonym match first, then a
    contains-match, so "Hotel Name (English)" still resolves.
    """
    lookup = {_key(h): h for h in headers}
    detected = {}
    claimed = set()

    # Pass 1 — exact synonym matches. These are unambiguous, so they claim the
    # column and later fields cannot steal it.
    for field, synonyms in COLUMN_SYNONYMS.items():
        for synonym in synonyms:
            if synonym in lookup and lookup[synonym] not in claimed:
                detected[field] = lookup[synonym]
                claimed.add(lookup[synonym])
                break

    # Pass 2 — substring matches, only on columns nothing has claimed. Without
    # the claim check, "supplier_name" matched "supplier_hotel_id" on the
    # substring "supplier" and imported hotel ids as supplier names.
    for field, synonyms in COLUMN_SYNONYMS.items():
        if field in detected:
            continue

        for key, original in lookup.items():
            if original in claimed:
                continue
            if any(synonym in key for synonym in synonyms):
                detected[field] = original
                claimed.add(original)
                break

    # A combined "18.5,73.8" column stands in for separate lat/lon.
    if "latlon" in detected:
        detected.pop("latitude", None)
        detected.pop("longitude", None)

    return detected


def _clean_postal(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    digits = re.sub(r"[^0-9]", "", str(value))
    return digits[:6] or None


def _to_float(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _text(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    cleaned = str(value).strip()
    return cleaned or None


class DataSourceService:
    """
    Import supplier hotels from an uploaded file or an external database.

    Every import is a two-step: analyse (detect columns, count issues, show a
    sample) then commit. Nothing is written until the detected mapping has been
    seen, because a silently mis-detected column produces thousands of unusable
    records.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    # ── loading ─────────────────────────────────────────────────────────────

    @staticmethod
    def read_upload(content: bytes, filename: str, sheet_name=None) -> pd.DataFrame:
        name = (filename or "").lower()

        if name.endswith((".xlsx", ".xls")):
            return pd.read_excel(
                io.BytesIO(content),
                sheet_name=0 if sheet_name is None else sheet_name,
            )

        for encoding in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                return pd.read_csv(io.BytesIO(content), encoding=encoding)
            except UnicodeDecodeError:
                continue

        raise ValueError("Could not read the file — unsupported encoding")

    @staticmethod
    def sheet_names(content: bytes, filename: str) -> list[str]:
        """
        The tabs in a workbook, in file order.

        One workbook holding one supplier per tab is how this data actually
        arrives. Reading only the first sheet silently imported one supplier and
        discarded the rest — with no error, because the first sheet parsed fine.
        """
        if not (filename or "").lower().endswith((".xlsx", ".xls")):
            return []

        return list(pd.ExcelFile(io.BytesIO(content)).sheet_names)

    async def analyse_workbook(self, content: bytes, filename: str) -> dict:
        """
        Analyse every sheet in one pass and suggest a supplier name for each.

        The tab name is the supplier name in practice ("Sabre", "GRN",
        "Booking.com"), so it is offered as the default and stays editable —
        guessing wrong here would file thousands of records under the wrong
        supplier, which is not something the importer should decide silently.
        """
        sheets = self.sheet_names(content, filename)

        if not sheets:
            raise ValueError("Not a workbook — a CSV has no sheets")

        reports = []

        for sheet in sheets:
            frame = pd.read_excel(io.BytesIO(content), sheet_name=sheet)

            if frame.empty:
                reports.append({
                    "sheet": sheet,
                    "suggested_supplier_name": str(sheet).strip(),
                    "total_rows": 0,
                    "ok": False,
                    "skip_reason": "The sheet is empty",
                })
                continue

            supplier = str(sheet).strip()

            report = await self.analyse_frame(frame, supplier)
            report["sheet"] = sheet
            report["suggested_supplier_name"] = supplier
            # Staged server-side like the single-file path. A workbook is the
            # case most likely to be large, so shipping every sheet back through
            # the browser was the worst version of the same problem. The blob is
            # content-addressed, so all sheets share one stored copy of the file.
            report["token"] = stage_upload(content, filename, sheet=sheet)

            reports.append(report)

        return {
            "filename": filename,
            "sheet_count": len(sheets),
            "sheets": reports,
            "importable": sum(1 for r in reports if r.get("ok")),
            "total_rows": sum(r.get("total_rows", 0) for r in reports),
            "already_present": sum(r.get("already_present", 0) for r in reports),
        }

    @staticmethod
    async def read_database(connection_url: str, query: str, limit: int = 200_000):
        """
        Pull rows from an external database.

        Only PostgreSQL and MySQL are accepted, and the statement must be a
        single read. This runs server-side, so it must never be exposed without
        authentication — see REVIEW_CONSOLE.md.
        """
        if not re.match(r"^(postgresql|postgres|mysql)(\+\w+)?://", connection_url):
            raise ValueError("Only postgresql:// or mysql:// connections are supported")

        statement = query.strip().rstrip(";")

        if not re.match(r"^\s*(select|with)\b", statement, re.IGNORECASE):
            raise ValueError("Only SELECT statements are allowed")

        if ";" in statement:
            raise ValueError("Only a single statement is allowed")

        import sqlalchemy

        engine = sqlalchemy.create_engine(connection_url, pool_pre_ping=True)

        try:
            with engine.connect() as connection:
                result = connection.execute(
                    sqlalchemy.text(f"SELECT * FROM ({statement}) src LIMIT {int(limit)}")
                )
                return pd.DataFrame(result.mappings().all())
        finally:
            engine.dispose()

    # ── analyse ─────────────────────────────────────────────────────────────

    async def analyse_frame(self, frame: pd.DataFrame, supplier_name: str,
                            mapping: dict = None) -> dict:
        """
        `analyse` plus the "have I imported this already?" count.

        Every analyse path must go through here. The duplicate count used to live
        in `analyse_workbook` alone, because `analyse` is sync and
        `count_already_imported` is not — so the single-file path, which is the
        one people actually use most, silently lacked the check the docstring on
        `count_already_imported` claims is "shown before the import runs". You
        got a clean "Import 8,432 rows" button for a file already fully in the
        database, and afterwards a report of 0 inserted with no reason given.

        Keeping the two paths on one method is the fix; splitting them is what
        let them drift.
        """
        report = self.analyse(frame, supplier_name, mapping)

        if not report.get("ok"):
            # Columns are missing, so the rows cannot be mapped and there is
            # nothing meaningful to compare against what is already stored.
            report["already_present"] = 0
            return report

        detected = mapping or detect_columns(list(frame.columns))
        rows = [
            row for row in (
                self._map_row(raw, detected, supplier_name)
                for _, raw in frame.iterrows()
            )
            if row is not None and row["hotel_name"] and row["country"]
        ]
        report["already_present"] = await self.count_already_imported(rows)

        return report

    def analyse(self, frame: pd.DataFrame, supplier_name: str, mapping: dict = None):
        headers = list(frame.columns)
        detected = mapping or detect_columns(headers)
        missing = [f for f in REQUIRED if f not in detected]

        issues = {
            "no_coordinates": 0,
            "no_city": 0,
            "no_postal_code": 0,
            "no_hotel_name": 0,
        }

        sample = []
        for _, raw in frame.head(400).iterrows():
            row = self._map_row(raw, detected, supplier_name)

            if row is None:
                continue

            if row["latitude"] is None or row["longitude"] is None:
                issues["no_coordinates"] += 1
            if not row["city"]:
                issues["no_city"] += 1
            if not row["postal_code"]:
                issues["no_postal_code"] += 1
            if not row["hotel_name"]:
                issues["no_hotel_name"] += 1

            if len(sample) < 8:
                sample.append(row)

        # Surface a supplier column in the file so the user can see it is being
        # overridden rather than discovering it afterwards.
        file_suppliers = []
        if "supplier_name" in detected:
            file_suppliers = sorted({
                str(v).strip() for v in frame[detected["supplier_name"]].dropna().unique()[:10]
            })

        return {
            "ok": not missing,
            "supplier_column_in_file": file_suppliers,
            "total_rows": int(len(frame)),
            "headers": headers,
            "detected": detected,
            "unmapped_headers": [h for h in headers if h not in detected.values()],
            "missing_required": missing,
            "issues_in_sample": issues,
            "sample_size": min(400, len(frame)),
            "sample": sample,
            "supplier_name": supplier_name,
        }

    def _map_row(self, raw, detected: dict, supplier_name: str):
        def field(name):
            column = detected.get(name)
            return raw.get(column) if column else None

        supplier_hotel_id = _text(field("supplier_hotel_id"))

        if not supplier_hotel_id:
            return None

        latitude = _to_float(field("latitude"))
        longitude = _to_float(field("longitude"))

        if latitude is None and "latlon" in detected:
            combined = _text(field("latlon")) or ""
            if "," in combined:
                parts = combined.split(",", 1)
                latitude = _to_float(parts[0])
                longitude = _to_float(parts[1])

        star = _to_float(field("star_rating"))
        if star is not None and not (0 <= star <= 5):
            star = None

        hotel_name = _text(field("hotel_name"))
        city = _text(field("city"))
        state = _text(field("state"))

        return {
            # The name the user typed is authoritative. A file often carries its
            # own supplier column, but silently preferring it means the user
            # types "Sabre Feb" and the rows land under something else.
            "supplier_name": supplier_name,
            "supplier_hotel_id": supplier_hotel_id,
            "hotel_name": hotel_name,
            "normalized_name": _text(field("normalized_name")),
            # Computed, never read from the file — see core_name in the schema.
            "core_name": core_hotel_name(hotel_name, city, state) or None,
            "strict_name": normalize_hotel_name(hotel_name) or None,
            "address": _text(field("address")),
            "city": city,
            "state": state,
            "country": _text(field("country")),
            "postal_code": _clean_postal(field("postal_code")),
            "latitude": latitude,
            "longitude": longitude,
            "star_rating": star,
        }

    # ── commit ──────────────────────────────────────────────────────────────

    async def commit(self, frame: pd.DataFrame, supplier_name: str,
                     mapping: dict = None, enqueue: bool = True):
        detected = mapping or detect_columns(list(frame.columns))
        missing = [f for f in REQUIRED if f not in detected]

        if missing:
            raise ValueError(
                "Cannot import — these fields were not found in the file: "
                + ", ".join(missing)
            )

        inserted = 0
        skipped = 0
        usable = 0
        batch = []

        for _, raw in frame.iterrows():
            row = self._map_row(raw, detected, supplier_name)

            if row is None or not row["hotel_name"] or not row["country"]:
                skipped += 1
                continue

            usable += 1
            batch.append(row)

            if len(batch) >= BATCH_SIZE:
                inserted += await self._insert_batch(batch, enqueue)
                batch = []

        if batch:
            inserted += await self._insert_batch(batch, enqueue)

        await self.session.commit()

        if inserted:
            # New records change what counts as a distinctive word for every
            # record already imported, so the corpus statistic is rebuilt now
            # rather than left for the next run to notice. The pipeline also
            # refreshes it before scoring; this keeps the Import screen honest
            # if anyone inspects the data in between.
            await self.session.execute(text("SELECT refresh_name_token_df();"))
            await self.session.commit()

        # Everything usable that did not land was already there.
        already_present = usable - inserted

        logger.info(
            "Imported %d rows for %s (%d unusable, %d already present)",
            inserted, supplier_name, skipped, already_present
        )

        return {
            "supplier_name": supplier_name,
            "total_rows": int(len(frame)),
            "inserted": inserted,
            "skipped": skipped,
            "already_present": already_present,
            "queued": inserted if enqueue else 0,
        }

    async def count_already_imported(self, rows: list[dict]) -> int:
        """
        How many of these rows are already in the database.

        Shown before the import runs. Re-importing a file used to silently
        double the data — 8,432 records became 16,864, every copy was flagged
        DUPLICATE_SUPPLIER_ROW, and the only visible symptom was a discard count
        that had quietly become larger than the dataset.
        """
        if not rows:
            return 0

        result = await self.session.execute(
            text(
                """
                SELECT count(*)
                FROM unnest(
                    CAST(:supplier_name     AS text[]),
                    CAST(:supplier_hotel_id AS text[]),
                    CAST(:hotel_name        AS text[])
                ) AS t(supplier_name, supplier_hotel_id, hotel_name)
                WHERE EXISTS (
                    SELECT 1 FROM supplier_hotels s
                    WHERE s.supplier_name     = t.supplier_name
                      AND s.supplier_hotel_id = t.supplier_hotel_id
                      AND s.hotel_name IS NOT DISTINCT FROM t.hotel_name
                );
                """
            ),
            {
                "supplier_name": [r["supplier_name"] for r in rows],
                "supplier_hotel_id": [r["supplier_hotel_id"] for r in rows],
                "hotel_name": [r["hotel_name"] for r in rows],
            },
        )

        return result.scalar() or 0

    async def _insert_batch(self, rows: list[dict], enqueue: bool) -> int:
        # A single set-based INSERT rather than executemany: RETURNING does not
        # yield rows under executemany, and we need the generated ids to queue
        # the records for mapping. unnest also keeps this to one round trip per
        # batch, which matters at 10 lakh records.
        result = await self.session.execute(
            text(
                """
                INSERT INTO supplier_hotels
                    (supplier_name, supplier_hotel_id, hotel_name, normalized_name,
                     core_name, strict_name, address, city, state, country,
                     postal_code, latitude, longitude, star_rating, geo_location)
                SELECT
                    t.supplier_name, t.supplier_hotel_id, t.hotel_name, t.normalized_name,
                    t.core_name, t.strict_name, t.address, t.city, t.state, t.country,
                    t.postal_code, t.latitude, t.longitude, t.star_rating,
                    CASE WHEN t.latitude IS NULL OR t.longitude IS NULL THEN NULL
                         ELSE ST_SetSRID(ST_MakePoint(t.longitude, t.latitude), 4326)::geography
                    END
                FROM unnest(
                    CAST(:supplier_name      AS text[]),
                    CAST(:supplier_hotel_id  AS text[]),
                    CAST(:hotel_name         AS text[]),
                    CAST(:normalized_name    AS text[]),
                    CAST(:core_name          AS text[]),
                    CAST(:strict_name        AS text[]),
                    CAST(:address            AS text[]),
                    CAST(:city               AS text[]),
                    CAST(:state              AS text[]),
                    CAST(:country            AS text[]),
                    CAST(:postal_code        AS text[]),
                    CAST(:latitude           AS double precision[]),
                    CAST(:longitude          AS double precision[]),
                    CAST(:star_rating        AS double precision[])
                ) AS t(supplier_name, supplier_hotel_id, hotel_name, normalized_name,
                       core_name, strict_name, address, city, state, country,
                       postal_code, latitude, longitude, star_rating)

                -- Importing the same file twice is a routine mistake — a
                -- re-run, a second person, a corrected sheet — and it used to
                -- double the dataset in silence. A row already present under
                -- the same supplier, id and name is skipped rather than
                -- inserted again. Cannot be a unique constraint: Sabre
                -- genuinely reuses one id across different hotels, and those
                -- rows must still import so the collision gets flagged.
                WHERE NOT EXISTS (
                    SELECT 1 FROM supplier_hotels s
                    WHERE s.supplier_name     = t.supplier_name
                      AND s.supplier_hotel_id = t.supplier_hotel_id
                      AND s.hotel_name IS NOT DISTINCT FROM t.hotel_name
                )
                RETURNING id;
                """
            ),
            {field: [row[field] for row in rows] for field in (
                "supplier_name", "supplier_hotel_id", "hotel_name", "normalized_name",
                "core_name", "strict_name", "address", "city", "state", "country",
                "postal_code", "latitude", "longitude", "star_rating",
            )},
        )

        ids = [r[0] for r in result.fetchall()]

        if enqueue and ids:
            await self.session.execute(
                text(
                    """
                    INSERT INTO hotel_mapping_queue (supplier_hotel_id, status)
                    SELECT unnest(CAST(:ids AS BIGINT[])), 'Pending';
                    """
                ),
                {"ids": ids},
            )

        return len(ids)
