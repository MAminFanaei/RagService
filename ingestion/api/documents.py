"""
Documents API — lifecycle management for ingested documents.

Endpoints:
  GET    /documents                     → paginated list with status filter
  GET    /documents/{doc_id}            → single document + chunk count
  DELETE /documents/{doc_id}            → delete file, chunks, ES vectors
  PATCH  /documents/{doc_id}            → update tags / custom metadata
  POST   /documents/{doc_id}/reindex    → re-run full pipeline from scratch
  GET    /metrics                       → ingestion health metrics
"""

from __future__ import annotations
from ingestion.storage import get_storage
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ingestion.tasks.ingestion_task import process_document
from ingestion.vector_store import get_vector_store
from ingestion.database import get_db
from ingestion.models import Chunk, Document, DocumentStatus

logger = structlog.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class DocumentOut(BaseModel):
    id: str
    original_filename: str
    mime_type: str
    file_size_bytes: int
    status: str
    error_message: str | None
    total_pages: int | None
    total_chunks: int | None
    detected_language: str | None
    parser_used: str | None
    ocr_used: str | None
    tags: list | None
    custom: dict | None
    ingestion_version: str
    storage_backend: str
    created_at: str
    processed_at: str | None

    class Config:
        from_attributes = True

    @classmethod
    def from_doc(cls, doc: Document, chunk_count: int | None = None) -> "DocumentOut":
        return cls(
            id=doc.id,
            original_filename=doc.original_filename,
            mime_type=doc.mime_type,
            file_size_bytes=doc.file_size_bytes,
            status=doc.status.value,
            error_message=doc.error_message,
            total_pages=doc.total_pages,
            total_chunks=chunk_count if chunk_count is not None else doc.total_chunks,
            detected_language=doc.detected_language,
            parser_used=doc.parser_used,
            ocr_used=doc.ocr_used,
            tags=doc.tags,
            custom=doc.custom,
            ingestion_version=doc.ingestion_version,
            storage_backend=doc.storage_backend,
            created_at=doc.created_at.isoformat() if doc.created_at else None,
            processed_at=doc.processed_at.isoformat() if doc.processed_at else None,
        )


class DocumentPatch(BaseModel):
    tags: list[str] | None = None
    custom: dict | None = None


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _get_doc_or_404(doc_id: str, db: AsyncSession) -> Document:
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    return doc


# ---------------------------------------------------------------------------
# GET /documents
# ---------------------------------------------------------------------------

@router.get("", summary="List documents")
async def list_documents(
    status: str | None = Query(None, description="Filter by status enum value"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    q = select(Document)
    if status:
        try:
            status_enum = DocumentStatus(status.upper())
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status '{status}'. Valid: {[s.value for s in DocumentStatus]}",
            )
        q = q.where(Document.status == status_enum)

    # Total
    count_result = await db.execute(
        select(func.count()).select_from(q.subquery())
    )
    total = count_result.scalar_one()

    # Paginated
    q = q.order_by(Document.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    doc_result = await db.execute(q)
    docs = doc_result.scalars().all()

    return {
        "total":     total,
        "page":      page,
        "page_size": page_size,
        "pages":     (total + page_size - 1) // page_size,
        "documents": [DocumentOut.from_doc(d) for d in docs],
    }


# ---------------------------------------------------------------------------
# GET /documents/{doc_id}
# ---------------------------------------------------------------------------

@router.get("/{doc_id}", summary="Get document detail")
async def get_document(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
):
    doc = await _get_doc_or_404(doc_id, db)

    # Actual chunk count from DB (more accurate than doc.total_chunks)
    count_result = await db.execute(
        select(func.count()).where(Chunk.doc_id == doc_id)
    )
    chunk_count = count_result.scalar_one()

    approved_result = await db.execute(
        select(func.count()).where(Chunk.doc_id == doc_id, Chunk.approved == True)  # noqa: E712
    )
    approved_count = approved_result.scalar_one()

    out = DocumentOut.from_doc(doc, chunk_count=chunk_count)
    return {**out.model_dump(), "approved_chunk_count": approved_count}


# ---------------------------------------------------------------------------
# DELETE /documents/{doc_id}
# ---------------------------------------------------------------------------

@router.delete("/{doc_id}", status_code=204, summary="Delete a document and all its data")
async def delete_document(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Full deletion:
      1. Delete ES vectors (delete_by_doc_id)
      2. SQL chunks deleted via CASCADE when Document is deleted
      3. Delete file from storage backend
      4. Delete Document row
    """
    doc = await _get_doc_or_404(doc_id, db)

    # 1. ES deletion (best-effort)
    try:
        vs = get_vector_store()
        deleted_es = await vs.delete_by_doc_id(doc_id)
        logger.info("doc_delete_es", doc_id=doc_id, deleted=deleted_es)
    except Exception as exc:
        logger.warning("doc_delete_es_failed", doc_id=doc_id, error=str(exc))

    # 2. File deletion (best-effort)
    try:
        storage = get_storage()
        await storage.delete(doc.storage_path)
        logger.info("doc_delete_file", doc_id=doc_id, path=doc.storage_path)
    except Exception as exc:
        logger.warning("doc_delete_file_failed", doc_id=doc_id, error=str(exc))

    # 3. SQL deletion (chunks cascade)
    await db.delete(doc)
    await db.commit()
    logger.info("doc_deleted", doc_id=doc_id)


# ---------------------------------------------------------------------------
# PATCH /documents/{doc_id}
# ---------------------------------------------------------------------------

@router.patch("/{doc_id}", response_model=DocumentOut, summary="Update document metadata")
async def patch_document(
    doc_id: str,
    body: DocumentPatch,
    db: AsyncSession = Depends(get_db),
):
    """
    Update tags and/or custom metadata.
    If tags change and document is READY, propagates tag update to ES via update_by_query.
    """
    doc = await _get_doc_or_404(doc_id, db)

    if body.tags is not None:
        doc.tags = body.tags
        # Propagate to ES if indexed
        if doc.status == DocumentStatus.READY:
            try:
                vs = get_vector_store()
                await vs.update_tags_by_doc_id(doc_id, body.tags)
            except Exception as exc:
                logger.warning("doc_patch_es_tags_failed", doc_id=doc_id, error=str(exc))

    if body.custom is not None:
        existing = doc.custom or {}
        doc.custom = {**existing, **body.custom}

    await db.commit()
    await db.refresh(doc)
    return DocumentOut.from_doc(doc)


# ---------------------------------------------------------------------------
# POST /documents/{doc_id}/reindex
# ---------------------------------------------------------------------------

@router.post("/{doc_id}/reindex", summary="Re-run the full ingestion pipeline")
async def reindex_document(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Full reindex:
      1. Delete existing ES vectors
      2. Delete existing SQL chunks
      3. Reset document status to PENDING
      4. Dispatch process_document task
    """
    doc = await _get_doc_or_404(doc_id, db)

    # 1. Delete from ES
    try:
        vs = get_vector_store()
        await vs.delete_by_doc_id(doc_id)
    except Exception as exc:
        logger.warning("reindex_es_delete_failed", doc_id=doc_id, error=str(exc))

    # 2. Delete chunks from SQL
    chunk_result = await db.execute(select(Chunk).where(Chunk.doc_id == doc_id))
    for chunk in chunk_result.scalars().all():
        await db.delete(chunk)

    # 3. Reset document
    doc.status        = DocumentStatus.PENDING
    doc.error_message = None
    doc.total_chunks  = None
    doc.parser_used   = None
    doc.ocr_used      = None
    doc.processed_at  = None
    await db.commit()

    # 4. Dispatch
    task = process_document.delay(doc_id)

    logger.info("doc_reindex_dispatched", doc_id=doc_id, task_id=task.id)
    return {"doc_id": doc_id, "task_id": task.id, "status": "queued"}


# ---------------------------------------------------------------------------
# GET /metrics
# ---------------------------------------------------------------------------

@router.get("/metrics", summary="Ingestion pipeline metrics")
async def get_metrics(db: AsyncSession = Depends(get_db)):
    """
    Returns aggregated ingestion metrics:
      - Document counts by status
      - Total chunks indexed
      - OCR invocation rate
      - Language distribution
      - Celery queue depth (requires Redis)
    """
    # Documents by status
    status_result = await db.execute(
        select(Document.status, func.count(Document.id))
        .group_by(Document.status)
    )
    documents_by_status = {
        row[0].value: row[1] for row in status_result.all()
    }

    # Total indexed chunks (vector_id is set)
    indexed_result = await db.execute(
        select(func.count(Chunk.id)).where(Chunk.vector_id.isnot(None))
    )
    total_chunks_indexed = indexed_result.scalar_one()

    # Total documents with OCR
    ocr_result = await db.execute(
        select(func.count(Document.id)).where(Document.ocr_used.isnot(None))
    )
    ocr_docs = ocr_result.scalar_one()
    total_docs = sum(documents_by_status.values())
    ocr_rate = round(ocr_docs / total_docs, 3) if total_docs else 0.0

    # Language distribution (from chunks)
    lang_result = await db.execute(
        select(Chunk.language, func.count(Chunk.id))
        .where(Chunk.language.isnot(None))
        .group_by(Chunk.language)
    )
    language_distribution = {row[0]: row[1] for row in lang_result.all()}

    # Queue depth via Redis (best-effort)
    queue_depth = 0
    try:
        from ingestion.config import get_settings
        import redis
        s = get_settings()
        r = redis.from_url(s.REDIS_URL, socket_timeout=2)
        queue_depth = r.llen("celery")  # default Celery queue name
        r.close()
    except Exception:
        pass

    return {
        "documents_by_status":   documents_by_status,
        "total_chunks_indexed":  total_chunks_indexed,
        "ocr_invocation_rate":   ocr_rate,
        "language_distribution": language_distribution,
        "queue_depth":           queue_depth,
    }
