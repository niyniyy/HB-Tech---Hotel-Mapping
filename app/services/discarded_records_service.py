import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Plain-language explanation per reason, shown directly in the UI. A reviewer
# should never have to ask an engineer what a flag means.
REASON_LABELS = {
    "SUPPLIER_ID_COLLISION": {
        "label": "Supplier reused one ID for two different hotels",
        "detail": "The supplier gave the same hotel ID to more than one property, "
                  "so we cannot tell which one a booking would mean. Raise with the supplier.",
        "severity": "high",
        "action": "Report to supplier",
    },
    "DUPLICATE_SUPPLIER_ROW": {
        "label": "Duplicate row in the supplier file",
        "detail": "The same hotel appears twice with the same ID. The first copy was "
                  "mapped; this one was skipped.",
        "severity": "low",
        "action": "No action needed",
    },
    "MISSING_COORDINATES": {
        "label": "No latitude/longitude",
        "detail": "Without coordinates we cannot confirm which building this is, "
                  "so it was not mapped.",
        "severity": "medium",
        "action": "Request coordinates from supplier",
    },
    "INVALID_COORDINATES": {
        "label": "Coordinates are 0,0",
        "detail": "Placeholder coordinates rather than a real location.",
        "severity": "medium",
        "action": "Request coordinates from supplier",
    },
    "COORDINATES_OUTSIDE_COUNTRY": {
        "label": "Coordinates fall outside the stated country",
        "detail": "The location does not match the country on the record.",
        "severity": "high",
        "action": "Request correction from supplier",
    },
    "MISSING_NAME": {"label": "No hotel name", "detail": "The record has no usable name.",
                     "severity": "high", "action": "Request correction from supplier"},
    "MISSING_CITY": {"label": "No city", "detail": "The record has no city.",
                     "severity": "medium", "action": "Request correction from supplier"},
    "MISSING_COUNTRY": {"label": "No country", "detail": "The record has no country.",
                        "severity": "medium", "action": "Request correction from supplier"},
    "NAME_NOT_DISTINGUISHING": {
        "label": "Name carries no identifying information",
        "detail": "After removing the city and generic words, nothing distinguishing "
                  "remained in the name.",
        "severity": "low",
        "action": "Request a fuller name from supplier",
    },
}


def describe(reason: str) -> dict:
    return REASON_LABELS.get(reason, {
        "label": reason.replace("_", " ").title(),
        "detail": "This record was not mapped.",
        "severity": "medium",
        "action": "Review",
    })


class DiscardedRecordsService:
    """
    Records excluded from mapping, and the notification state around them.

    A record dropped silently is a record nobody fixes. Every exclusion is
    surfaced with a plain-language reason and stays unacknowledged until
    somebody has actually looked at it.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def summary(self):
        result = await self.session.execute(
            text(
                """
                SELECT f.flag_reason,
                       count(*) AS total,
                       count(*) FILTER (WHERE f.acknowledged_at IS NULL) AS unread,
                       count(DISTINCT s.supplier_name) AS suppliers
                FROM flagged_records f
                JOIN supplier_hotels s ON s.id = f.supplier_hotel_id
                GROUP BY f.flag_reason
                ORDER BY count(*) FILTER (WHERE f.acknowledged_at IS NULL) DESC;
                """
            )
        )

        rows = []
        for row in result.mappings().all():
            entry = dict(row)
            entry.update(describe(entry["flag_reason"]))
            rows.append(entry)

        unread = sum(r["unread"] for r in rows)

        return {
            "reasons": rows,
            "total": sum(r["total"] for r in rows),
            "unread": unread,
            "has_notifications": unread > 0,
        }

    async def by_supplier(self):
        result = await self.session.execute(
            text(
                """
                SELECT s.supplier_name,
                       count(*) AS discarded,
                       count(*) FILTER (WHERE f.acknowledged_at IS NULL) AS unread,
                       (SELECT count(*) FROM supplier_hotels x
                         WHERE x.supplier_name = s.supplier_name) AS total_supplied
                FROM flagged_records f
                JOIN supplier_hotels s ON s.id = f.supplier_hotel_id
                GROUP BY s.supplier_name
                ORDER BY count(*) DESC;
                """
            )
        )
        return [dict(r) for r in result.mappings().all()]

    async def list_records(self, reason: str = None, unread_only: bool = False,
                           limit: int = 100, offset: int = 0):
        result = await self.session.execute(
            text(
                """
                SELECT f.id, f.flag_reason, f.created_at, f.acknowledged_at,
                       s.id AS supplier_hotel_row_id, s.supplier_name,
                       s.supplier_hotel_id, s.hotel_name, s.address, s.city,
                       s.country, s.postal_code, s.latitude, s.longitude
                FROM flagged_records f
                JOIN supplier_hotels s ON s.id = f.supplier_hotel_id
                WHERE (CAST(:reason AS VARCHAR) IS NULL
                       OR f.flag_reason = CAST(:reason AS VARCHAR))
                  AND (NOT CAST(:unread_only AS BOOLEAN)
                       OR f.acknowledged_at IS NULL)
                ORDER BY f.acknowledged_at NULLS FIRST, f.id DESC
                LIMIT :limit OFFSET :offset;
                """
            ),
            {"reason": reason, "unread_only": unread_only,
             "limit": limit, "offset": offset}
        )

        rows = []
        for row in result.mappings().all():
            entry = dict(row)
            entry.update(describe(entry["flag_reason"]))
            rows.append(entry)
        return rows

    async def acknowledge(self, record_ids: list[int], actor: str = "reviewer"):
        if not record_ids:
            return {"acknowledged": 0}

        await self.session.execute(
            text(
                """
                UPDATE flagged_records
                SET acknowledged_at = NOW(), acknowledged_by = :actor
                WHERE id = ANY(:ids) AND acknowledged_at IS NULL;
                """
            ),
            {"ids": record_ids, "actor": actor}
        )
        await self.session.commit()
        return {"acknowledged": len(record_ids)}

    async def acknowledge_all(self, reason: str = None, actor: str = "reviewer"):
        await self.session.execute(
            text(
                """
                UPDATE flagged_records
                SET acknowledged_at = NOW(), acknowledged_by = :actor
                WHERE acknowledged_at IS NULL
                  AND (CAST(:reason AS VARCHAR) IS NULL
                       OR flag_reason = CAST(:reason AS VARCHAR));
                """
            ),
            {"reason": reason, "actor": actor}
        )
        await self.session.commit()
        return {"ok": True}
