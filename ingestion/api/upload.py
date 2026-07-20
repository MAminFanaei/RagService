"""
Upload API — POST /documents and POST /documents/bulk

Flow for single upload:
  1. Read file bytes from multipart form
  2. Call intake_file() → validates, deduplicates, stores, creates Document
  3. If accepted → dispatch process_document Celery task
  4. Return {doc_id, task_id, status}

Flow for bulk upload:
  - Runs intake + dispatch per file in order
  - Per-file errors are captured and returned (batch never fails entirely)
  - Returns list of per-file results

Auth: requires is_admin=True (JWT from main app).
      For now the dependency is a stub — replace with real JWT dep when
      the main app's auth dependency is importable here.
"""

from __future__ import annotations

import json
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from ingestion.tasks.ingestion_task import process_document
from ingestion.database import get_db
from ingestion.intake import intake_file

logger = structlog.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Auth dependency stub
# ---------------------------------------------------------------------------

async def require_admin():
    """
    Placeholder admin auth dependency.

    Replace the body with a real JWT check once the main app's
    security module is importable from here, e.g.:
        from app.api.deps import get_current_admin_user
        return await get_current_admin_user(token=token)

    For now this is a no-op so the service starts without the main app
    auth machinery being available.
    """
    return True


# ---------------------------------------------------------------------------
# Single upload
# ---------------------------------------------------------------------------

@router.post("", summary="Upload a single document")
async def upload_document(
    file: UploadFile = File(..., description="Document file to ingest"),
    tags: str | None = Form(
        None,
        description='JSON array of tags e.g. ["legal", "Q4-2024"]',
    ),
    chunking_strategy: str | None = Form(
        None,
        description="Override chunking strategy: auto|heading|semantic|sentence|fixed|hierarchical",
    ),
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
):
    """
    Upload a single document for ingestion.

    Returns immediately after queuing — use GET /tasks/{task_id} to poll progress.
    """
    data = await file.read()
    filename = file.filename or "upload"

    # Parse tags JSON if provided
    tag_list: list[str] | None = None
    if tags:
        try:
            tag_list = json.loads(tags)
            if not isinstance(tag_list, list):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            raise HTTPException(
                status_code=422,
                detail="tags must be a JSON array of strings, e.g. [\"legal\"]",
            )

    # Custom metadata: pass chunking_strategy override if provided
    custom: dict | None = None
    if chunking_strategy:
        custom = {"chunking_strategy": chunking_strategy}

    # Intake: validate, dedup, store, create Document row
    intake_result = await intake_file(
        data=data,
        filename=filename,
        db=db,
        tags=tag_list,
        custom=custom,
    )

    doc_id = intake_result["doc_id"]

    if intake_result["status"] == "duplicate":
        logger.info("upload_duplicate", doc_id=doc_id, filename=filename)
        return {
            "doc_id":  doc_id,
            "task_id": None,
            "status":  "duplicate",
            "message": "File already exists in the system",
        }

    # Dispatch background processing task
    
    task = process_document.delay(doc_id)

    logger.info(
        "upload_accepted",
        doc_id=doc_id,
        task_id=task.id,
        filename=filename,
        mime=intake_result["mime_type"],
    )

    return {
        "doc_id":      doc_id,
        "task_id":     task.id,
        "status":      "queued",
        "mime_type":   intake_result["mime_type"],
        "parser_type": intake_result["parser_type"],
    }


# ---------------------------------------------------------------------------
# Bulk upload
# ---------------------------------------------------------------------------

@router.post("/bulk", summary="Upload multiple documents in one request")
async def upload_documents_bulk(
    files: list[UploadFile] = File(..., description="Documents to ingest"),
    tags: str | None = Form(None, description='JSON array of tags for ALL files'),
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
):
    """
    Upload multiple documents at once.

    Per-file errors are included in the response — the batch never fails entirely.
    """
    tag_list: list[str] | None = None
    if tags:
        try:
            tag_list = json.loads(tags)
            if not isinstance(tag_list, list):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            raise HTTPException(
                status_code=422,
                detail="tags must be a JSON array of strings",
            )

    results = []
    for upload in files:
        filename = upload.filename or "upload"
        try:
            data = await upload.read()
            intake_result = await intake_file(
                data=data,
                filename=filename,
                db=db,
                tags=tag_list,
            )
            doc_id = intake_result["doc_id"]

            if intake_result["status"] == "duplicate":
                results.append({
                    "filename": filename,
                    "doc_id":   doc_id,
                    "task_id":  None,
                    "status":   "duplicate",
                })
                continue

            task = process_document.delay(doc_id)
            results.append({
                "filename":  filename,
                "doc_id":    doc_id,
                "task_id":   task.id,
                "status":    "queued",
                "mime_type": intake_result["mime_type"],
            })

        except HTTPException as exc:
            results.append({
                "filename": filename,
                "doc_id":   None,
                "task_id":  None,
                "status":   "error",
                "error":    exc.detail,
            })
        except Exception as exc:
            logger.exception("bulk_upload_file_error", filename=filename)
            results.append({
                "filename": filename,
                "doc_id":   None,
                "task_id":  None,
                "status":   "error",
                "error":    str(exc),
            })

    return {"results": results, "total": len(results)}
