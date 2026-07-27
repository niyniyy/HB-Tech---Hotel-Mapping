"""
Server-side staging for the two-step import.

The import is analyse-then-commit, and the commit needs the same data the
analyse step reported on. Two earlier designs failed here, both for the same
underlying reason — they moved a *re-encoded copy* of the data instead of the
data.

  1. The frame was base64'd into the browser and posted back as a form field.
     A form field is not a file and the transport caps it, so large uploads
     arrived truncated.

  2. The frame was written server-side as JSON. That fixed size, but JSON is not
     a faithful container for a DataFrame: any integer above uint64 max — a long
     numeric supplier id, a GST number, a barcode — is written as a bare JSON
     number that pandas then refuses to read back with "Value is too big!".

So this stores the ORIGINAL UPLOADED BYTES and re-parses them at commit with the
same reader the analyse step used. There is no serialisation round trip left to
lose or mangle anything: commit sees byte-identical input, parsed by identical
code, and dtypes cannot drift between the two steps either.

Blobs are content-addressed, so a workbook staging one token per sheet writes the
file once and points every sheet's tiny metadata record at that single copy.

Staged uploads DO expire — that is what STAGING_TTL_SECONDS is for, and it is the
one case where telling someone the import "expired" is honest.

DEPLOYMENT NOTE: staging is local disk, so analyse and commit must reach the same
process. That holds for the single API container this repo ships. Behind a load
balancer with more than one replica, a commit routed to a different replica finds
no staged file and reports the import as expired — correctly, but confusingly.
Running more than one replica needs shared storage here (a mounted volume, S3, or
a staging table) rather than tempfile.gettempdir().
"""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

STAGING_DIR = Path(tempfile.gettempdir()) / "hb_import_staging"

# Long enough to read the analysis, think, and decide; short enough that
# abandoned uploads do not accumulate.
STAGING_TTL_SECONDS = 6 * 60 * 60


class StagedFrameMissing(Exception):
    """No staged upload for this key — expired, purged, or never written."""


class StagedFrameUnreadable(Exception):
    """The staged files exist but could not be read back."""


def _purge_expired() -> None:
    """
    Drop metadata past its TTL, then any blob nothing points at.

    Blob-last, and by reference rather than by age: several sheets of one
    workbook share a blob, so deleting blobs on their own mtime would strand the
    sheets that were still live.
    """
    cutoff = time.time() - STAGING_TTL_SECONDS

    try:
        STAGING_DIR.mkdir(parents=True, exist_ok=True)

        for meta_path in STAGING_DIR.glob("*.meta.json"):
            try:
                if meta_path.stat().st_mtime < cutoff:
                    meta_path.unlink()
            except OSError:
                continue

        referenced = set()
        for meta_path in STAGING_DIR.glob("*.meta.json"):
            try:
                referenced.add(json.loads(meta_path.read_text())["blob"])
            except (OSError, ValueError, KeyError):
                continue

        for blob in STAGING_DIR.glob("*.bin"):
            if blob.stem not in referenced:
                try:
                    blob.unlink()
                except OSError:
                    continue
    except OSError:
        pass


def stage_upload(content: bytes, filename: str, sheet: str | None = None) -> str:
    """
    Persist an uploaded file (and which sheet of it to read) and return the
    short key that retrieves it.
    """
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    _purge_expired()

    digest = hashlib.sha256(content).hexdigest()
    blob = STAGING_DIR / f"{digest}.bin"

    if not blob.exists():
        # Written under a temp name and moved into place, so a concurrent
        # reader can never observe a half-written blob.
        partial = blob.with_suffix(".partial")
        partial.write_bytes(content)
        partial.replace(blob)

    token = uuid.uuid4().hex
    (STAGING_DIR / f"{token}.meta.json").write_text(
        json.dumps({"blob": digest, "filename": filename, "sheet": sheet})
    )

    logger.info(
        "Staged import %s -> blob %s (%s, sheet=%s, %d bytes)",
        token, digest[:12], filename, sheet, len(content),
    )

    return token


def load_staged_upload(token: str) -> tuple[bytes, str, str | None]:
    """
    Read back a staged upload as (content, filename, sheet).

    Returns raw bytes rather than a DataFrame so this module needs no reader —
    the caller parses with the same function the analyse step used, which is the
    property that makes the two steps agree.

    Raises StagedFrameMissing or StagedFrameUnreadable so a caller can tell an
    upload that aged out from an actual fault; the single catch-all this
    replaced could not distinguish them.
    """
    # The key becomes a filesystem path, so it is validated rather than trusted:
    # a token like "../../etc/passwd" must not resolve to anything.
    if not token or len(token) != 32 or not all(c in "0123456789abcdef" for c in token):
        raise StagedFrameMissing(f"malformed import key {token!r}")

    meta_path = STAGING_DIR / f"{token}.meta.json"

    if not meta_path.exists():
        raise StagedFrameMissing(f"no staged upload for {token}")

    try:
        meta = json.loads(meta_path.read_text())
        blob = STAGING_DIR / f"{meta['blob']}.bin"
    except (OSError, ValueError, KeyError) as error:
        raise StagedFrameUnreadable(f"staging metadata is corrupt: {error}") from error

    if not blob.exists():
        raise StagedFrameMissing(f"staged file for {token} has been purged")

    try:
        return blob.read_bytes(), meta.get("filename") or "", meta.get("sheet")
    except OSError as error:
        raise StagedFrameUnreadable(str(error)) from error


def discard_staged(token: str) -> None:
    """
    Drop a staged upload once committed. Best-effort.

    Only the metadata is removed here; the blob goes when the next purge finds
    nothing referencing it, because a sibling sheet of the same workbook may
    still be waiting to be committed.
    """
    try:
        (STAGING_DIR / f"{token}.meta.json").unlink(missing_ok=True)
    except OSError:
        pass
