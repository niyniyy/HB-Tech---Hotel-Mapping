from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import get_db
from app.services.master_lifecycle_service import MasterLifecycleService
from app.services.discarded_records_service import DiscardedRecordsService
from app.services.master_identity_service import MasterIdentityService
from app.services.integrity_monitor import IntegrityMonitor
from app.services.provisional_master_service import ProvisionalMasterService
from app.services.accuracy_evaluation_service import AccuracyEvaluationService
from app.services.search_service import SearchService

router = APIRouter(prefix="/api/v1", tags=["Review"])


class SplitRequest(BaseModel):
    supplier_row_ids: list[int] = Field(..., min_length=1)
    reason: str = Field(..., min_length=3)
    actor: str = "reviewer"


class DeprecateRequest(BaseModel):
    reason: str = Field(..., min_length=3)
    case: str = "Closed"          # Closed | Dormant
    actor: str = "reviewer"


class ReactivateRequest(BaseModel):
    reason: str = Field(..., min_length=3)
    actor: str = "reviewer"


class AcknowledgeRequest(BaseModel):
    record_ids: list[int] = []
    reason: str | None = None
    all: bool = False
    actor: str = "reviewer"


class MergeRequest(BaseModel):
    into: str = Field(..., min_length=3)
    reason: str = Field(..., min_length=3)
    actor: str = "reviewer"
    # Overrides a previous split. Never defaulted on: re-merging what a
    # reviewer separated must be a deliberate act.
    force: bool = False


class ConfirmRequest(BaseModel):
    reason: str = Field(..., min_length=3)
    actor: str = "reviewer"


# ── dashboard ───────────────────────────────────────────────────────────────

@router.get("/dashboard")
async def dashboard(db: AsyncSession = Depends(get_db)):
    stats = await db.execute(
        text(
            """
            SELECT
              (SELECT count(*) FROM supplier_hotels)                              AS supplier_hotels,
              (SELECT count(*) FROM master_hotel_registry WHERE status='Active')  AS active_masters,
              (SELECT count(*) FROM master_hotel_registry WHERE status='Merged')  AS merged_ids,
              (SELECT count(*) FROM master_hotel_registry
                WHERE status IN ('Deprecated','Dormant'))                         AS deprecated_ids,
              (SELECT count(*) FROM hotel_mappings WHERE mapping_type='AUTO')     AS auto_mapped,
              (SELECT count(*) FROM hotel_mappings
                WHERE mapping_type IN ('NEW_MASTER','MANUAL_NEW_MASTER'))          AS new_masters,
              (SELECT count(*) FROM manual_review_candidates)                     AS pending_review,
              (SELECT count(*) FROM flagged_records)                              AS discarded,
              (SELECT count(*) FROM flagged_records WHERE acknowledged_at IS NULL) AS discarded_unread,
              (SELECT count(*) FROM hotel_mapping_queue WHERE status='Pending')   AS queue_pending,
              (SELECT count(*) FROM hotel_mapping_queue WHERE status='Failed')    AS queue_failed,
              (SELECT count(*) FROM master_non_merge_assertion)                   AS reviewer_assertions,
              -- Only ids that actually have a master behind them. After a
              -- rebuild an id whose seed record is sitting in the review queue
              -- is Provisional with no master; it resolves itself when the
              -- reviewer decides, and counting it here would inflate the badge
              -- past the number of rows the page can show.
              (SELECT count(*) FROM master_hotel_registry
                WHERE status='Provisional' AND master_hotel_id IS NOT NULL)       AS provisional_masters,
              (SELECT count(*) FROM master_merge_decision)                        AS reviewer_merges;
            """
        )
    )

    tiers = await db.execute(
        text(
            """
            SELECT coalesce(confidence_tier,'UNKNOWN') AS tier, count(*) AS n
            FROM hotel_mappings WHERE mapping_type='AUTO'
            GROUP BY 1 ORDER BY 1;
            """
        )
    )

    suppliers = await db.execute(
        text(
            """
            SELECT s.supplier_name,
                   count(*) AS supplied,
                   count(hm.id) AS mapped,
                   count(*) FILTER (WHERE hm.mapping_type = 'AUTO')
                       AS auto_mapped,
                   count(*) FILTER (WHERE hm.mapping_type = 'NEW_MASTER')
                       AS new_master,
                   count(*) FILTER (WHERE hm.mapping_type = 'MANUAL')
                       AS manual_mapped,
                   count(*) FILTER (WHERE hm.mapping_type = 'MANUAL_NEW_MASTER')
                       AS manual_new_master,
                   count(*) FILTER (WHERE f.id IS NOT NULL) AS discarded,
                   round(count(hm.id)::numeric * 100
                         / nullif(count(*), 0), 1) AS mapped_pct
            FROM supplier_hotels s
            LEFT JOIN hotel_mappings hm ON hm.supplier_hotel_row_id = s.id
            LEFT JOIN flagged_records f ON f.supplier_hotel_id = s.id
            GROUP BY s.supplier_name ORDER BY count(*) DESC;
            """
        )
    )

    return {
        "stats": dict(stats.mappings().first()),
        "confidence": [dict(r) for r in tiers.mappings().all()],
        "suppliers": [dict(r) for r in suppliers.mappings().all()],
    }


# ── discarded records (notifications) ───────────────────────────────────────

@router.get("/discarded/summary")
async def discarded_summary(db: AsyncSession = Depends(get_db)):
    return await DiscardedRecordsService(db).summary()


@router.get("/discarded/by-supplier")
async def discarded_by_supplier(db: AsyncSession = Depends(get_db)):
    return {"suppliers": await DiscardedRecordsService(db).by_supplier()}


@router.get("/discarded")
async def discarded_list(
    reason: str | None = None,
    unread_only: bool = False,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    limit = max(1, min(limit, 500))
    records = await DiscardedRecordsService(db).list_records(
        reason=reason, unread_only=unread_only, limit=limit, offset=offset
    )
    return {"records": records, "count": len(records)}


@router.post("/discarded/acknowledge")
async def discarded_acknowledge(
    payload: AcknowledgeRequest, db: AsyncSession = Depends(get_db)
):
    service = DiscardedRecordsService(db)

    if payload.all:
        return await service.acknowledge_all(payload.reason, payload.actor)

    return await service.acknowledge(payload.record_ids, payload.actor)


# ── masters ─────────────────────────────────────────────────────────────────

SEARCH_FIELDS = (
    "q", "master_id", "provider_hotel_id", "provider_name",
    "chain_name", "property_type", "country", "city", "star_min",
)


@router.get("/search/facets")
async def search_facets(db: AsyncSession = Depends(get_db)):
    """Dropdown values, plus which filters currently have no data behind them."""
    return await SearchService(db).facets()


@router.get("/masters/search")
async def masters_search(
    q: str | None = None,
    master_id: str | None = None,
    provider_hotel_id: str | None = None,
    provider_name: str | None = None,
    chain_name: str | None = None,
    property_type: str | None = None,
    country: str | None = None,
    city: str | None = None,
    star_min: float | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """
    Multi-field search. Every filter is optional and they combine with AND;
    results come back as soon as any one of them has a value.

    Rows are supplier records grouped under their master hotel id, so searching
    one id shows every provider record for that property side by side.
    """
    params = {k: v for k, v in locals().items() if k in SEARCH_FIELDS}
    return await SearchService(db).search_records(
        params, limit=max(1, min(limit, 500)), offset=max(0, offset)
    )


@router.get("/masters/count")
async def masters_count(
    q: str | None = None,
    master_id: str | None = None,
    provider_hotel_id: str | None = None,
    provider_name: str | None = None,
    chain_name: str | None = None,
    property_type: str | None = None,
    country: str | None = None,
    city: str | None = None,
    star_min: float | None = None,
    db: AsyncSession = Depends(get_db),
):
    params = {k: v for k, v in locals().items() if k in SEARCH_FIELDS}
    return await SearchService(db).count_records(params)


@router.get("/records/{supplier_row_id}/duplicates")
async def record_duplicates(supplier_row_id: int, db: AsyncSession = Depends(get_db)):
    """Other hotels near this one that may be the same property."""
    return await SearchService(db).find_duplicates(supplier_row_id)


@router.get("/review-queue/search")
async def manual_review_search(
    q: str | None = None, limit: int = 200, db: AsyncSession = Depends(get_db)
):
    """Search the review queue across every field at once."""
    return await SearchService(db).search_manual_review(q, max(1, min(limit, 500)))


@router.get("/masters/{public_id}")
async def master_detail(public_id: str, db: AsyncSession = Depends(get_db)):
    detail = await MasterLifecycleService(db).get_master_detail(public_id)

    if detail is None:
        raise HTTPException(status_code=404, detail="Master hotel not found")

    return detail


@router.post("/masters/{public_id}/split")
async def master_split(
    public_id: str, payload: SplitRequest, db: AsyncSession = Depends(get_db)
):
    result = await MasterLifecycleService(db).split_master(
        public_id, payload.supplier_row_ids, payload.reason, payload.actor
    )

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))

    return result


@router.post("/masters/{public_id}/deprecate")
async def master_deprecate(
    public_id: str, payload: DeprecateRequest, db: AsyncSession = Depends(get_db)
):
    result = await MasterLifecycleService(db).deprecate_master(
        public_id, payload.reason, payload.case, payload.actor
    )

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))

    return result


@router.post("/masters/{public_id}/reactivate")
async def master_reactivate(
    public_id: str, payload: ReactivateRequest, db: AsyncSession = Depends(get_db)
):
    result = await MasterLifecycleService(db).reactivate_master(
        public_id, payload.reason, payload.actor
    )

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))

    return result


@router.get("/masters/{public_id}/resolve")
async def master_resolve(public_id: str, db: AsyncSession = Depends(get_db)):
    """
    Resolve any public id — current or retired — to the live one.
    Consumers holding a merged id call this rather than getting a 404.
    """
    resolved = await MasterIdentityService(db).resolve(public_id)

    if resolved is None:
        raise HTTPException(status_code=404, detail="Unknown public id")

    return {
        "requested": public_id,
        "resolved": resolved,
        "redirected": resolved != public_id,
    }


@router.post("/masters/{public_id}/merge")
async def master_merge(
    public_id: str, payload: MergeRequest, db: AsyncSession = Depends(get_db)
):
    """
    Fold this master into another — the reviewer's verdict that they are one
    property. Resolves both the provisional queue and exact-name duplicates.
    """
    result = await MasterLifecycleService(db).merge_masters(
        public_id, payload.into, payload.reason, payload.actor, payload.force
    )

    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error"))

    return result


# ── provisional masters ─────────────────────────────────────────────────────

@router.get("/provisional/summary")
async def provisional_summary(db: AsyncSession = Depends(get_db)):
    return await ProvisionalMasterService(db).summary()


@router.get("/provisional")
async def provisional_queue(
    limit: int = 100,
    offset: int = 0,
    suspicious_only: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """
    Single-supplier masters awaiting corroboration, most suspicious first.

    `suspicious_only` narrows to the ones with a same-named neighbour nearby —
    the probable duplicates, as opposed to genuine single-supplier properties.
    """
    rows = await ProvisionalMasterService(db).queue(limit, offset, suspicious_only)

    return {"results": rows, "count": len(rows), "limit": limit, "offset": offset}


@router.get("/provisional/{public_id}/suggestions")
async def provisional_suggestions(
    public_id: str, limit: int = 10, db: AsyncSession = Depends(get_db)
):
    """The masters this provisional one might actually be."""
    result = await ProvisionalMasterService(db).suggestions(public_id, limit)

    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error"))

    return result


@router.post("/provisional/{public_id}/confirm")
async def provisional_confirm(
    public_id: str, payload: ConfirmRequest, db: AsyncSession = Depends(get_db)
):
    """
    Publish a single-supplier master on the reviewer's authority: a real
    property that only one supplier happens to carry.
    """
    result = await MasterIdentityService(db).confirm_master(
        public_id, payload.reason, payload.actor
    )

    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error"))

    return result


# ── integrity ───────────────────────────────────────────────────────────────

@router.get("/duplicates/count")
async def duplicates_count(db: AsyncSession = Depends(get_db)):
    """Just the number of duplicate master pairs, for the dashboard tile."""
    dup = await ProvisionalMasterService(db).fragmented_masters(limit=1000)
    return {"duplicate_pairs": dup["count"]}


@router.get("/fragmentation")
async def fragmentation(limit: int = 200, db: AsyncSession = Depends(get_db)):
    """
    Masters already in the table that look like one hotel filed twice.

    The exact-name rules only govern records processed from now on; this is how
    the duplicates that predate them get found.
    """
    return await ProvisionalMasterService(db).fragmented_masters(limit)


@router.get("/integrity")
async def integrity(db: AsyncSession = Depends(get_db)):
    return await IntegrityMonitor(db).run_all()


# ── accuracy against a reference mapping ────────────────────────────────────

@router.get("/evaluation/accuracy")
async def evaluation_accuracy(db: AsyncSession = Depends(get_db)):
    """
    Precision and recall against the loaded reference mapping.

    The only number here that measures correctness rather than self-consistency.
    Run it after every pipeline change: a threshold that improves one figure
    almost always costs the other, and that trade has to be visible.
    """
    return await AccuracyEvaluationService(db).evaluate()


@router.get("/evaluation/false-merges")
async def evaluation_false_merges(limit: int = 100, db: AsyncSession = Depends(get_db)):
    """Masters holding more than one reference hotel — the errors that matter."""
    rows = await AccuracyEvaluationService(db).false_merges(max(1, min(limit, 500)))
    return {"false_merges": rows, "count": len(rows)}


@router.get("/evaluation/splits")
async def evaluation_splits(limit: int = 200, db: AsyncSession = Depends(get_db)):
    """Reference hotels spread across several masters, worst first."""
    rows = await AccuracyEvaluationService(db).splits(max(1, min(limit, 1000)))
    return {"splits": rows, "count": len(rows)}


@router.get("/evaluation/gate")
async def evaluation_gate(db: AsyncSession = Depends(get_db)):
    """The release gate: are there any wrong merges in the published set?"""
    return await AccuracyEvaluationService(db).gate_status()
