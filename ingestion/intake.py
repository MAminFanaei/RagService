"""
File intake — the very first step in the ingestion pipeline.

intake_file() is the single entry point for any file entering the system.
It performs, in order:
  1. True MIME detection from raw bytes (python-magic, not extension)
  2. Allowlist check                → HTTPException 415 if unsupported
  3. Size check                     → HTTPException 413 if too large
  4. SHA-256 content hash
  5. Duplicate detection via DB:
       READY   → return early with duplicate status (no reprocessing)
       FAILED  → delete stale record + file, then re-ingest
       missing → proceed normally
  6. Build storage path: {year}/{month}/{doc_id}/{original_filename}
  7. Persist file bytes via storage backend
  8. INSERT Document row (status=PENDING)
  9. Return intake result dict

No Celery dispatch here — the caller (upload API) dispatches the task
after receiving the result dict.
"""

import hashlib
import logging
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ingestion.config import settings
from ingestion.models import Document, DocumentStatus
from ingestion.storage import get_storage

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# MIME → parser-type mapping (allowlist)                                       #
# --------------------------------------------------------------------------- #

MIME_TO_PARSER: dict[str, str] = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "text/html": "html",
    "text/plain": "text",
    "text/markdown": "text",
    "image/png": "ocr_direct",
    "image/jpeg": "ocr_direct",
    "image/tiff": "ocr_direct",
}

_MAX_BYTES = settings.MAX_UPLOAD_FILE_SIZE_MB * 1024 * 1024


# --------------------------------------------------------------------------- #
# MIME detection helper                                                        #
# --------------------------------------------------------------------------- #

def _detect_mime(data: bytes) -> str:
    """
    Detect the true MIME type from the first bytes of the file.

    Uses python-magic (libmagic) which reads magic bytes — immune to
    extension spoofing (e.g. renamed .exe → .pdf).

    Falls back to 'application/octet-stream' if magic is unavailable
    so tests can run without the system library installed.
    """
    try:
        import magic  # python-magic
        return magic.from_buffer(data[:8192], mime=True)
    except ImportError:
        logger.warning(
            "python-magic not installed; falling back to octet-stream detection. "
            "Run: pip install python-magic (and: apt install libmagic1)"
        )
        return "application/octet-stream"


# --------------------------------------------------------------------------- #
# Main intake function                                                         #
# --------------------------------------------------------------------------- #

async def intake_file(
    data: bytes,
    filename: str,
    db: AsyncSession,
    tags: list[str] | None = None,
    custom: dict | None = None,
) -> dict:
    """
    Validate, deduplicate, store, and register an uploaded file.

    Parameters
    ----------
    data     : raw file bytes
    filename : original filename (used for storage path + Document record)
    db       : async SQLAlchemy session (injected by FastAPI get_db dep)
    tags     : optional list of string tags  e.g. ["legal", "Q4-2024"]
    custom   : optional free-form metadata dict

    Returns
    -------
    dict with keys:
        status       : "accepted" | "duplicate"
        doc_id       : UUID string
        mime_type    : detected MIME
        storage_path : relative path where file was saved   (absent on duplicate)
        parser_type  : which parser family will handle it   (absent on duplicate)
    """

    # ------------------------------------------------------------------ #
    # 1. Detect true MIME                                                  #
    # ------------------------------------------------------------------ #
    mime_type = _detect_mime(data)
    logger.debug("intake: detected mime=%s for file=%s", mime_type, filename)

    # ------------------------------------------------------------------ #
    # 2. Allowlist check                                                   #
    # ------------------------------------------------------------------ #
    parser_type = MIME_TO_PARSER.get(mime_type)
    if parser_type is None:
        logger.warning("intake: unsupported mime=%s file=%s", mime_type, filename)
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type: '{mime_type}'. "
                f"Accepted types: {sorted(MIME_TO_PARSER.keys())}"
            ),
        )

    # ------------------------------------------------------------------ #
    # 3. Size check                                                         #
    # ------------------------------------------------------------------ #
    if len(data) > _MAX_BYTES:
        size_mb = len(data) / (1024 * 1024)
        logger.warning(
            "intake: file too large size_mb=%.1f limit_mb=%d file=%s",
            size_mb, settings.MAX_UPLOAD_FILE_SIZE_MB, filename,
        )
        raise HTTPException(
            status_code=413,
            detail=(
                f"File too large: {size_mb:.1f} MB. "
                f"Maximum allowed: {settings.MAX_UPLOAD_FILE_SIZE_MB} MB."
            ),
        )

    # ------------------------------------------------------------------ #
    # 4. SHA-256 content hash                                              #
    # ------------------------------------------------------------------ #
    content_hash = hashlib.sha256(data).hexdigest()

    # ------------------------------------------------------------------ #
    # 5. Duplicate detection                                               #
    # ------------------------------------------------------------------ #
    result = await db.execute(
        select(Document).where(Document.content_hash == content_hash)
    )
    existing: Document | None = result.scalar_one_or_none()

    if existing is not None:
        if existing.status == DocumentStatus.READY:
            # Already fully indexed — return early, no reprocessing
            logger.info(
                "intake: duplicate detected doc_id=%s file=%s",
                existing.id, filename,
            )
            return {
                "status": "duplicate",
                "doc_id": existing.id,
                "mime_type": mime_type,
                "parser_type": parser_type,
            }

        if existing.status == DocumentStatus.FAILED:
            # Previous attempt failed — delete stale record and re-ingest
            logger.info(
                "intake: re-ingesting previously failed doc_id=%s file=%s",
                existing.id, filename,
            )
            storage = get_storage()
            # Best-effort deletion of stale file; don't crash if missing
            try:
                await storage.delete(existing.storage_path)
            except Exception as exc:
                logger.warning("intake: could not delete stale file: %s", exc)
            await db.delete(existing)
            await db.flush()
            # Fall through to create a fresh Document below

        # Any other status (PENDING / PROCESSING / REVIEW / INDEXING):
        # Another worker is handling it — return duplicate to avoid races
        elif existing.status not in (DocumentStatus.FAILED,):
            logger.info(
                "intake: file already in-flight status=%s doc_id=%s",
                existing.status.value, existing.id,
            )
            return {
                "status": "duplicate",
                "doc_id": existing.id,
                "mime_type": mime_type,
                "parser_type": parser_type,
            }

    # ------------------------------------------------------------------ #
    # 6. Build storage path                                                #
    # ------------------------------------------------------------------ #
    import uuid
    doc_id = str(uuid.uuid4())
    now = datetime.now(tz=timezone.utc)
    storage_path = f"{now.year}/{now.month:02d}/{doc_id}/{filename}"

    # ------------------------------------------------------------------ #
    # 7. Persist file bytes                                                #
    # ------------------------------------------------------------------ #
    storage = get_storage()
    await storage.save(data, storage_path)
    logger.info(
        "intake: file saved path=%s doc_id=%s", storage_path, doc_id
    )

    # ------------------------------------------------------------------ #
    # 8. Insert Document row                                               #
    # ------------------------------------------------------------------ #
    document = Document(
        id=doc_id,
        original_filename=filename,
        mime_type=mime_type,
        file_size_bytes=len(data),
        content_hash=content_hash,
        storage_backend=settings.STORAGE_BACKEND,
        storage_path=storage_path,
        status=DocumentStatus.PENDING,
        tags=tags,
        custom=custom,
        ingestion_version=settings.INGESTION_VERSION,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    logger.info(
        "intake: document registered doc_id=%s mime=%s parser=%s",
        doc_id, mime_type, parser_type,
    )

    # ------------------------------------------------------------------ #
    # 9. Return result                                                     #
    # ------------------------------------------------------------------ #
    return {
        "status": "accepted",
        "doc_id": doc_id,
        "mime_type": mime_type,
        "storage_path": storage_path,
        "parser_type": parser_type,
    }
