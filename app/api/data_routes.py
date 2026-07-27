import json
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import get_db
from app.services.data_source_service import DataSourceService
from app.services.export_service import ExportService
from app.services.import_staging import (
    StagedFrameMissing,
    StagedFrameUnreadable,
    discard_staged,
    load_staged_upload,
    stage_upload,
)
from app.services.queue_processing_service import STALE_CLAIM_MINUTES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Data"])

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_UPLOAD_BYTES = 120 * 1024 * 1024


class DatabaseSource(BaseModel):
    connection_url: str = Field(..., min_length=10)
    query: str = Field(..., min_length=6)
    supplier_name: str = Field(..., min_length=1)
    limit: int = 200_000


class DatabaseCommit(DatabaseSource):
    mapping: dict | None = None
    enqueue: bool = True


class RunRequest(BaseModel):
    limit: int = 500


# ── export ──────────────────────────────────────────────────────────────────

@router.get("/export")
async def export_list():
    """Datasets available for download."""
    return {"datasets": ExportService.available()}


@router.get("/export/{dataset}")
async def export_dataset(dataset: str, limit: int = 200_000,
                         db: AsyncSession = Depends(get_db)):
    stream, filename = await ExportService(db).build(dataset, limit)

    if stream is None:
        raise HTTPException(status_code=404, detail=f"Unknown dataset '{dataset}'")

    return StreamingResponse(
        stream,
        media_type=XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── import: file ────────────────────────────────────────────────────────────

@router.post("/import/file/analyse")
async def import_file_analyse(
    supplier_name: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Read the file, detect which column is which, and report what would happen.
    Nothing is written.
    """
    content = await file.read()

    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is larger than 120 MB")

    try:
        frame = DataSourceService.read_upload(content, file.filename)
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Could not read file: {error}")

    report = await DataSourceService(db).analyse_frame(frame, supplier_name)

    # Keep the uploaded file on the server and hand back a short key. Posting the
    # whole frame back through a form field is what made large files fail.
    report["token"] = stage_upload(content, file.filename)
    report["filename"] = file.filename

    return report


@router.post("/import/file/workbook")
async def import_workbook_analyse(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Analyse every sheet of a workbook at once.

    For the common case where one file holds all suppliers, one tab each.
    Returns a per-sheet report with its own token, so the sheets are committed
    independently and one bad tab does not block the rest.
    """
    content = await file.read()

    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is larger than 120 MB")

    try:
        return await DataSourceService(db).analyse_workbook(content, file.filename)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Could not read workbook: {error}")


@router.post("/import/file/commit")
async def import_file_commit(
    supplier_name: str = Form(...),
    token: str = Form(...),
    mapping: str = Form(None),
    enqueue: bool = Form(True),
    db: AsyncSession = Depends(get_db),
):
    # Each failure is reported as itself. The previous single catch-all called
    # every one of them "session expired" and told the user to re-upload — advice
    # that could not work, because the usual cause was the file being too large
    # to survive the round trip, which re-uploading reproduces exactly.
    if not token or token == "undefined":
        raise HTTPException(
            status_code=400,
            detail="No import key was sent. Press 'Check the file' again, then Import.",
        )

    try:
        content, filename, sheet = load_staged_upload(token)
        # Parsed with the same reader the analyse step used, over the same bytes,
        # so the two steps cannot disagree about types or values.
        frame = DataSourceService.read_upload(content, filename, sheet_name=sheet)
    except StagedFrameMissing:
        raise HTTPException(
            status_code=400,
            detail=(
                "This import has expired — staged files are kept for 6 hours and "
                "are cleared when the server restarts. Press 'Check the file' "
                "again to re-stage it."
            ),
        )
    except StagedFrameUnreadable as error:
        logger.exception("Staged import frame %s could not be read", token)
        raise HTTPException(
            status_code=500,
            detail=f"The staged import data could not be read back: {error}",
        )
    except Exception as error:
        # Re-parsing the staged bytes failed. Still logged in full and reported
        # as itself rather than folded into "expired" — the analyse step already
        # parsed these exact bytes, so reaching here means something genuinely
        # unexpected, and hiding it would put us back where this started.
        logger.exception("Staged upload %s could not be parsed at commit", token)
        raise HTTPException(
            status_code=500,
            detail=(
                f"The staged file could not be re-read: "
                f"{type(error).__name__}: {error}"
            ),
        )

    try:
        result = await DataSourceService(db).commit(
            frame, supplier_name,
            json.loads(mapping) if mapping else None,
            enqueue,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    # Only once the rows are safely in: a failed commit keeps the staged frame so
    # the user can fix the mapping and retry without re-uploading.
    discard_staged(token)

    return result


# ── import: database ────────────────────────────────────────────────────────

@router.post("/import/database/analyse")
async def import_db_analyse(payload: DatabaseSource, db: AsyncSession = Depends(get_db)):
    try:
        frame = await DataSourceService.read_database(
            payload.connection_url, payload.query, payload.limit
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Could not connect or query: {error}")

    if frame.empty:
        raise HTTPException(status_code=400, detail="The query returned no rows")

    report = await DataSourceService(db).analyse_frame(frame, payload.supplier_name)
    report["source"] = "database"
    report["rows_fetched"] = int(len(frame))

    return report


@router.post("/import/database/commit")
async def import_db_commit(payload: DatabaseCommit, db: AsyncSession = Depends(get_db)):
    try:
        frame = await DataSourceService.read_database(
            payload.connection_url, payload.query, payload.limit
        )
        result = await DataSourceService(db).commit(
            frame, payload.supplier_name, payload.mapping, payload.enqueue
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Import failed: {error}")

    return result


# ── pipeline control ────────────────────────────────────────────────────────

@router.post("/pipeline/run")
async def pipeline_run(payload: RunRequest, db: AsyncSession = Depends(get_db)):
    from app.jobs.mapping_worker import process_queue_batch

    # Abandoned claims count as waiting. A worker that dies mid-batch leaves its
    # rows in Processing, and a gate that only sees Pending then refuses to
    # start — so the one action that could recover those records is the one the
    # screen will not let you take.
    pending = await db.execute(
        text(
            f"""
            SELECT count(*) FROM hotel_mapping_queue
            WHERE status = 'Pending'
               OR (status = 'Processing'
                   AND claimed_at < NOW()
                       - make_interval(mins => {STALE_CLAIM_MINUTES}))
            """
        )
    )
    waiting = pending.scalar()

    if waiting == 0:
        return {"started": False, "message": "Nothing is waiting to be processed."}

    task = process_queue_batch.delay(limit=payload.limit, apply_decision=True)

    return {"started": True, "task_id": task.id, "pending": waiting}


@router.get("/pipeline/status")
async def pipeline_status(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text(
            """
            SELECT
              count(*) FILTER (WHERE status = 'Pending')      AS pending,
              count(*) FILTER (WHERE status = 'Processing')   AS processing,
              count(*) FILTER (WHERE status = 'Processing'
                                 AND claimed_at < NOW()
                                     - make_interval(mins => 15)) AS stalled,
              count(*) FILTER (WHERE status = 'Completed')    AS completed,
              count(*) FILTER (WHERE status = 'Flagged')      AS flagged,
              count(*) FILTER (WHERE status = 'Failed')       AS failed,
              count(*)                                        AS total
            FROM hotel_mapping_queue;
            """
        )
    )

    status = dict(result.mappings().first())
    done = status["completed"] + status["flagged"] + status["failed"]

    status["running"] = status["processing"] > 0 or status["pending"] > 0
    status["progress_pct"] = round(100.0 * done / status["total"], 1) if status["total"] else 100.0

    return status


@router.post("/pipeline/requeue-failed")
async def pipeline_requeue_failed(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text(
            """
            UPDATE hotel_mapping_queue
            SET status = 'Pending', retry_count = retry_count + 1
            WHERE status = 'Failed'
            RETURNING id;
            """
        )
    )
    count = len(result.fetchall())
    await db.commit()

    return {"requeued": count}
