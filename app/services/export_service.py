import io
import logging
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.publication_gate import publishable_sql

logger = logging.getLogger(__name__)

MAX_ROWS = 200_000

# Every exportable dataset. Adding one here makes it available in the API and
# the console without further changes.
DATASETS = {
    "master-hotels": {
        "title": "Master Hotels",
        "sheet": "Master Hotels",
        "sql": """
            SELECT r.public_id            AS "Hotel ID",
                   r.status               AS "Status",
                   m.hotel_name           AS "Hotel Name",
                   m.address              AS "Address",
                   m.city                 AS "City",
                   m.state                AS "State",
                   m.country              AS "Country",
                   m.postal_code          AS "Postal Code",
                   m.latitude             AS "Latitude",
                   m.longitude            AS "Longitude",
                   m.star_rating          AS "Star Rating",
                   (SELECT count(*) FROM hotel_mappings hm
                     WHERE hm.master_hotel_id = m.master_hotel_id
                       AND {PUBLISHABLE}) AS "Supplier Records",
                   r.superseded_by        AS "Merged Into",
                   r.deprecation_reason   AS "Closure Reason",
                   m.created_at           AS "Created"
            FROM master_hotel_registry r
            JOIN master_hotels m ON m.master_hotel_id = r.master_hotel_id
            -- Provisional masters are excluded from every export. A master
            -- seeded by one supplier and corroborated by nobody is a claim, not
            -- a property, and this file is what downstream systems treat as the
            -- authoritative hotel list.
            WHERE r.status <> 'Provisional'
            ORDER BY r.public_id
        """,
    },
    "mappings": {
        "title": "Supplier to Master Mappings",
        "sheet": "Mappings",
        "sql": """
            SELECT r.public_id             AS "Hotel ID",
                   m.hotel_name            AS "Master Hotel",
                   s.supplier_name         AS "Supplier",
                   s.supplier_hotel_id     AS "Supplier Hotel ID",
                   s.hotel_name            AS "Supplier Hotel Name",
                   s.address               AS "Supplier Address",
                   s.city                  AS "City",
                   hm.mapping_type         AS "Mapping Type",
                   hm.confidence_tier      AS "Confidence",
                   hm.match_score          AS "Score",
                   hm.name_similarity      AS "Name Match %",
                   hm.distance_meters      AS "Distance (m)",
                   hm.is_manual_verified   AS "Human Verified",
                   hm.created_at           AS "Mapped At"
            FROM hotel_mappings hm
            JOIN supplier_hotels s ON s.id = hm.supplier_hotel_row_id
            JOIN master_hotels m ON m.master_hotel_id = hm.master_hotel_id
            LEFT JOIN master_hotel_registry r
                   ON r.master_hotel_id = hm.master_hotel_id
            WHERE (r.status IS NULL OR r.status <> 'Provisional')
              AND {PUBLISHABLE}
            ORDER BY r.public_id, s.supplier_name
        """,
    },
    "discarded-records": {
        "title": "Discarded Records",
        "sheet": "Discarded",
        "sql": """
            SELECT f.flag_reason          AS "Reason Code",
                   s.supplier_name        AS "Supplier",
                   s.supplier_hotel_id    AS "Supplier Hotel ID",
                   s.hotel_name           AS "Hotel Name",
                   s.address              AS "Address",
                   s.city                 AS "City",
                   s.state                AS "State",
                   s.country              AS "Country",
                   s.postal_code          AS "Postal Code",
                   s.latitude             AS "Latitude",
                   s.longitude            AS "Longitude",
                   CASE WHEN f.acknowledged_at IS NULL
                        THEN 'Not reviewed' ELSE 'Reviewed' END AS "Review Status",
                   f.acknowledged_by      AS "Reviewed By",
                   f.created_at           AS "Discarded At"
            FROM flagged_records f
            JOIN supplier_hotels s ON s.id = f.supplier_hotel_id
            ORDER BY f.flag_reason, s.supplier_name
        """,
    },
    "manual-review": {
        "title": "Manual Review Queue",
        "sheet": "Manual Review",
        "sql": """
            SELECT s.supplier_name        AS "Supplier",
                   s.supplier_hotel_id    AS "Supplier Hotel ID",
                   s.hotel_name           AS "Supplier Hotel Name",
                   s.city                 AS "City",
                   m.hotel_name           AS "Suggested Match",
                   r.public_id            AS "Suggested Hotel ID",
                   c.rule_score           AS "Score",
                   c.ai_similarity        AS "AI Similarity",
                   c.decision_reason      AS "Reason",
                   c.created_at           AS "Queued At"
            FROM manual_review_candidates c
            JOIN supplier_hotels s ON s.id = c.supplier_hotel_id
            JOIN master_hotels m ON m.master_hotel_id = c.suggested_master_hotel_id
            LEFT JOIN master_hotel_registry r
                   ON r.master_hotel_id = c.suggested_master_hotel_id
            ORDER BY c.rule_score DESC
        """,
    },
    "supplier-hotels": {
        "title": "Supplier Hotels (raw imported)",
        "sheet": "Supplier Hotels",
        "sql": """
            SELECT s.supplier_name        AS "Supplier",
                   s.supplier_hotel_id    AS "Supplier Hotel ID",
                   s.hotel_name           AS "Hotel Name",
                   s.address              AS "Address",
                   s.city                 AS "City",
                   s.state                AS "State",
                   s.country              AS "Country",
                   s.postal_code          AS "Postal Code",
                   s.latitude             AS "Latitude",
                   s.longitude            AS "Longitude",
                   s.star_rating          AS "Star Rating",
                   q.status               AS "Processing Status",
                   r.public_id            AS "Mapped To Hotel ID"
            FROM supplier_hotels s
            LEFT JOIN hotel_mapping_queue q ON q.supplier_hotel_id = s.id
            LEFT JOIN hotel_mappings hm ON hm.supplier_hotel_row_id = s.id
            LEFT JOIN master_hotel_registry r ON r.master_hotel_id = hm.master_hotel_id
            ORDER BY s.supplier_name, s.supplier_hotel_id
        """,
    },
    "supplier-quality": {
        "title": "Supplier Data Quality",
        "sheet": "Supplier Quality",
        "sql": """
            SELECT s.supplier_name                                   AS "Supplier",
                   count(*)                                          AS "Records Supplied",
                   count(hm.id)                                      AS "Mapped",
                   count(*) FILTER (WHERE f.id IS NOT NULL)          AS "Discarded",
                   round(100.0 * count(*) FILTER (WHERE f.id IS NOT NULL)
                         / NULLIF(count(*), 0), 2)                   AS "Discard Rate %",
                   count(*) FILTER (WHERE s.latitude IS NULL)        AS "Missing Coordinates",
                   count(*) FILTER (WHERE s.postal_code IS NULL)     AS "Missing Postal Code",
                   count(*) FILTER (WHERE s.star_rating IS NULL)     AS "Missing Star Rating"
            FROM supplier_hotels s
            LEFT JOIN hotel_mappings hm ON hm.supplier_hotel_row_id = s.id
            LEFT JOIN flagged_records f ON f.supplier_hotel_id = s.id
            GROUP BY s.supplier_name
            ORDER BY count(*) DESC
        """,
    },
    "integrity": {
        "title": "Integrity — masters worth reviewing",
        "sheet": "Integrity",
        "sql": """
            SELECT r.public_id       AS "Hotel ID",
                   m.hotel_name      AS "Hotel Name",
                   m.city            AS "City",
                   count(*)          AS "Supplier Records",
                   count(DISTINCT s.supplier_name) AS "Suppliers",
                   round(max(ST_Distance(s.geo_location, m.geo_location))::numeric, 1)
                                     AS "Widest Spread (m)",
                   round(min(hm.name_similarity), 1) AS "Worst Name Match %"
            FROM hotel_mappings hm
            JOIN master_hotels m ON m.master_hotel_id = hm.master_hotel_id
            JOIN supplier_hotels s ON s.id = hm.supplier_hotel_row_id
            LEFT JOIN master_hotel_registry r ON r.master_hotel_id = m.master_hotel_id
            GROUP BY r.public_id, m.hotel_name, m.city
            HAVING max(ST_Distance(s.geo_location, m.geo_location)) > 400
                OR min(hm.name_similarity) < 60
            ORDER BY max(ST_Distance(s.geo_location, m.geo_location)) DESC
        """,
    },
    "reviewer-decisions": {
        "title": "Reviewer Decisions (permanent)",
        "sheet": "Reviewer Decisions",
        "sql": """
            SELECT a.supplier_name    AS "Supplier A",
                   a.hotel_name       AS "Hotel A",
                   b.supplier_name    AS "Supplier B",
                   b.hotel_name       AS "Hotel B",
                   n.reason           AS "Reason",
                   n.asserted_by      AS "Decided By",
                   n.created_at       AS "Decided At"
            FROM master_non_merge_assertion n
            JOIN supplier_hotels a ON a.id = n.row_id_a
            JOIN supplier_hotels b ON b.id = n.row_id_b
            ORDER BY n.created_at DESC
        """,
    },
    "supplier-id-conflicts": {
        "title": "Supplier ID Conflicts",
        "sheet": "ID Conflicts",
        "sql": """
            SELECT a.supplier_name AS "Supplier",
                   a.supplier_hotel_id AS "Supplier Hotel ID",
                   a.hotel_name   AS "Hotel A",
                   a.city         AS "City A",
                   a.latitude     AS "Latitude A",
                   a.longitude    AS "Longitude A",
                   b.hotel_name   AS "Hotel B",
                   b.city         AS "City B",
                   b.latitude     AS "Latitude B",
                   b.longitude    AS "Longitude B",
                   round(ST_Distance(a.geo_location, b.geo_location)::numeric, 0)
                                  AS "Metres Apart"
            FROM supplier_hotels a
            JOIN supplier_hotels b
              ON a.supplier_name = b.supplier_name
             AND a.supplier_hotel_id = b.supplier_hotel_id
             AND a.id < b.id
            WHERE lower(a.hotel_name) <> lower(b.hotel_name)
            ORDER BY ST_Distance(a.geo_location, b.geo_location) DESC
        """,
    },
}

HEADER_FILL = PatternFill("solid", fgColor="1F4D8F")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)


class ExportService:
    """
    Excel export for every section of the console.

    One writer for all datasets: the SQL carries the human-readable column names,
    so a new export needs only an entry in DATASETS.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def available():
        return [
            {"key": key, "title": spec["title"]}
            for key, spec in DATASETS.items()
        ]

    async def build(self, dataset: str, limit: int = MAX_ROWS):
        spec = DATASETS.get(dataset)

        if spec is None:
            return None, None

        # The publication gate is substituted here rather than written into each
        # query, so a tier policy change cannot reach one export and miss
        # another.
        sql = spec["sql"].replace("{PUBLISHABLE}", publishable_sql("hm"))

        result = await self.session.execute(
            text(f"SELECT * FROM ({sql}) q LIMIT :limit"),
            {"limit": min(limit, MAX_ROWS)},
        )

        rows = result.mappings().all()
        columns = list(rows[0].keys()) if rows else ["No data"]

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = spec["sheet"][:31]

        sheet.append(columns)
        for cell in sheet[1]:
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = Alignment(vertical="center")

        for row in rows:
            sheet.append([
                value.replace(tzinfo=None) if isinstance(value, datetime) else value
                for value in row.values()
            ])

        # Width from the longest value in each column, within sane bounds.
        for index, column in enumerate(columns, start=1):
            widest = max(
                [len(str(column))] +
                [len(str(r[column])) for r in rows[:400] if r[column] is not None]
                or [len(str(column))]
            )
            sheet.column_dimensions[get_column_letter(index)].width = min(max(widest + 2, 10), 52)

        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions

        stream = io.BytesIO()
        workbook.save(stream)
        stream.seek(0)

        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        filename = f"{dataset}-{stamp}.xlsx"

        logger.info("Exported %s (%d rows)", dataset, len(rows))

        return stream, filename
