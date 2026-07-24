import base64
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

    report = DataSourceService(db).analyse(frame, supplier_name)

    # Hand the parsed data back so the commit step need not re-upload it.
    report["token"] = base64.b64encode(
        frame.to_json(orient="split", date_format="iso").encode()
    ).decode()
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
    import pandas as pd

    try:
        frame = pd.read_json(base64.b64decode(token).decode(), orient="split")
    except Exception:
        raise HTTPException(status_code=400, detail="Import session expired — re-upload the file")

    try:
        result = await DataSourceService(db).commit(
            frame, supplier_name,
            json.loads(mapping) if mapping else None,
            enqueue,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

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

    report = DataSourceService(db).analyse(frame, payload.supplier_name)
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
