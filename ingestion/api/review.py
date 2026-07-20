"""
Review API — human-in-the-loop chunk review before ES indexing.

Endpoints:
  GET    /documents/{doc_id}/chunks          → paginated chunk list
  PATCH  /documents/{doc_id}/chunks/{chunk_id} → edit chunk text/metadata
  DELETE /documents/{doc_id}/chunks/{chunk_id} → remove a chunk
  POST   /documents/{doc_id}/chunks          → add a manual chunk
  POST   /documents/{doc_id}/approve         → approve selected chunks + dispatch index task
  POST   /documents/{doc_id}/approve-all     → mark all approved + dispatch index task

All endpoints require document to exist and belong to the right status.
PATCH sets edited_by_admin=True for audit trail.
approve validates all approved chunks have non-empty text before dispatching.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from ingestion.tasks.ingestion_task import index_document
from ingestion.database import get_db
from ingestion.models import Chunk, Document, DocumentStatus

logger = structlog.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ChunkOut(BaseModel):
    id: str
    doc_id: str
    page_number: int | None
    section_title: str | None
    section_path: list | None
    element_type: str
    is_table: bool
    is_footnote: bool
    text: str
    char_count: int
    token_estimate: int | None
    language: str | None
    script_direction: str | None
    chunk_index: int
    total_chunks: int | None
    vector_id: str | None
    approved: bool
    edited_by_admin: bool

    class Config:
        from_attributes = True


class ChunkPatch(BaseModel):
    text: str | None = Field(None, description="New chunk text")
    element_type: str | None = Field(None, description="Override element type")
    section_title: str | None = Field(None, description="Override section title")
    approved: bool | None = Field(None, description="Approval flag")


class ChunkCreate(BaseModel):
    text: str = Field(..., min_length=1)
    element_type: str = "text"
    page_number: int | None = None
    section_title: str | None = None
    section_path: list[str] | None = None
    is_table: bool = False
    language: str | None = None


class ApproveRequest(BaseModel):
    chunk_ids: list[str] | None = Field(
        None,
        description="Specific chunk IDs to approve. If None, approves all chunks.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_doc_or_404(doc_id: str, db: AsyncSession) -> Document:
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    return doc


async def _get_chunk_or_404(chunk_id: str, doc_id: str, db: AsyncSession) -> Chunk:
    result = await db.execute(
        select(Chunk).where(Chunk.id == chunk_id, Chunk.doc_id == doc_id)
    )
    chunk = result.scalar_one_or_none()
    if chunk is None:
        raise HTTPException(
            status_code=404,
            detail=f"Chunk {chunk_id} not found in document {doc_id}",
        )
    return chunk


# ---------------------------------------------------------------------------
# GET /documents/{doc_id}/chunks
# ---------------------------------------------------------------------------

@router.get("/{doc_id}/chunks", summary="List chunks for review")
async def list_chunks(
    doc_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    approved: bool | None = Query(None, description="Filter by approval status"),
    element_type: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Return paginated chunks for a document.
    Available at any status (REVIEW, INDEXING, READY).
    """
    await _get_doc_or_404(doc_id, db)

    q = select(Chunk).where(Chunk.doc_id == doc_id)
    if approved is not None:
        q = q.where(Chunk.approved == approved)
    if element_type:
        q = q.where(Chunk.element_type == element_type)

    # Total count
    count_q = select(func.count()).select_from(q.subquery())
    total_result = await db.execute(count_q)
    total = total_result.scalar_one()

    # Paginated results ordered by chunk_index
    q = q.order_by(Chunk.chunk_index).offset((page - 1) * page_size).limit(page_size)
    chunk_result = await db.execute(q)
    chunks = chunk_result.scalars().all()

    return {
        "doc_id":    doc_id,
        "total":     total,
        "page":      page,
        "page_size": page_size,
        "pages":     (total + page_size - 1) // page_size,
        "chunks":    [ChunkOut.model_validate(c) for c in chunks],
    }


# ---------------------------------------------------------------------------
# PATCH /documents/{doc_id}/chunks/{chunk_id}
# ---------------------------------------------------------------------------

@router.patch("/{doc_id}/chunks/{chunk_id}", response_model=ChunkOut,
              summary="Edit a chunk")
async def patch_chunk(
    doc_id: str,
    chunk_id: str,
    body: ChunkPatch,
    db: AsyncSession = Depends(get_db),
):
    """
    Edit chunk text, element_type, section_title, or approval status.
    Sets edited_by_admin=True when text or element_type changes.
    """
    await _get_doc_or_404(doc_id, db)
    chunk = await _get_chunk_or_404(chunk_id, doc_id, db)

    edited = False
    if body.text is not None and body.text != chunk.text:
        chunk.text      = body.text
        chunk.char_count = len(body.text)
        chunk.token_estimate = max(1, len(body.text.split()) * 13 // 10)
        edited = True

    if body.element_type is not None:
        chunk.element_type = body.element_type
        edited = True

    if body.section_title is not None:
        chunk.section_title = body.section_title

    if body.approved is not None:
        chunk.approved = body.approved

    if edited:
        chunk.edited_by_admin = True

    await db.commit()
    await db.refresh(chunk)

    logger.info("chunk_patched", doc_id=doc_id, chunk_id=chunk_id, edited=edited)
    return ChunkOut.model_validate(chunk)


# ---------------------------------------------------------------------------
# DELETE /documents/{doc_id}/chunks/{chunk_id}
# ---------------------------------------------------------------------------

@router.delete("/{doc_id}/chunks/{chunk_id}", status_code=204,
               summary="Delete a chunk")
async def delete_chunk(
    doc_id: str,
    chunk_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Remove a chunk from SQL.
    Only valid before indexing (REVIEW stage) — chunk is not in ES yet.
    If the chunk has a vector_id, also removes it from ES.
    """
    await _get_doc_or_404(doc_id, db)
    chunk = await _get_chunk_or_404(chunk_id, doc_id, db)

    # If already indexed, remove from ES too
    if chunk.vector_id:
        try:
            from ingestion.vector_store import get_vector_store
            # ES delete by _id not supported directly by our interface;
            # use delete_by_query on chunk_id keyword
            vs = get_vector_store()
            es = vs._get_es()
            await es.delete_by_query(
                index=vs._index,
                body={"query": {"term": {"chunk_id": chunk.id}}},
                refresh=True,
            )
        except Exception as exc:
            logger.warning("chunk_delete_es_failed", chunk_id=chunk_id, error=str(exc))

    await db.delete(chunk)
    await db.commit()
    logger.info("chunk_deleted", doc_id=doc_id, chunk_id=chunk_id)


# ---------------------------------------------------------------------------
# POST /documents/{doc_id}/chunks  (add manual chunk)
# ---------------------------------------------------------------------------

@router.post("/{doc_id}/chunks", response_model=ChunkOut, status_code=201,
             summary="Add a manual chunk")
async def create_chunk(
    doc_id: str,
    body: ChunkCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    Manually add a chunk to a document.
    Useful for injecting corrections or additional context.
    """
    doc = await _get_doc_or_404(doc_id, db)

    # Get current max chunk_index
    count_result = await db.execute(
        select(func.count()).where(Chunk.doc_id == doc_id)
    )
    count = count_result.scalar_one()

    chunk = Chunk(
        doc_id=doc_id,
        text=body.text,
        char_count=len(body.text),
        token_estimate=max(1, len(body.text.split()) * 13 // 10),
        element_type=body.element_type,
        page_number=body.page_number,
        section_title=body.section_title,
        section_path=body.section_path,
        is_table=body.is_table,
        language=body.language,
        chunk_index=count,
        approved=False,
        edited_by_admin=True,  # manually created = admin action
    )
    db.add(chunk)
    await db.commit()
    await db.refresh(chunk)

    logger.info("chunk_created_manually", doc_id=doc_id, chunk_id=chunk.id)
    return ChunkOut.model_validate(chunk)


# ---------------------------------------------------------------------------
# POST /documents/{doc_id}/approve
# ---------------------------------------------------------------------------

@router.post("/{doc_id}/approve", summary="Approve selected chunks and start indexing")
async def approve_chunks(
    doc_id: str,
    body: ApproveRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Mark specified chunks (or all chunks if chunk_ids is None) as approved=True,
    then dispatch the index_document Celery task.

    Validates:
      - Document must be in REVIEW status
      - All approved chunks must have non-empty text
    """
    doc = await _get_doc_or_404(doc_id, db)

    if doc.status not in (DocumentStatus.REVIEW,):
        raise HTTPException(
            status_code=409,
            detail=f"Document is in status '{doc.status.value}', expected REVIEW",
        )

    # Determine which chunks to approve
    if body.chunk_ids is not None:
        q = select(Chunk).where(
            Chunk.doc_id == doc_id,
            Chunk.id.in_(body.chunk_ids),
        )
    else:
        q = select(Chunk).where(Chunk.doc_id == doc_id)

    chunk_result = await db.execute(q)
    chunks = list(chunk_result.scalars().all())

    if not chunks:
        raise HTTPException(status_code=400, detail="No chunks found to approve")

    # Validate: no empty text
    empty_ids = [c.id for c in chunks if not c.text.strip()]
    if empty_ids:
        raise HTTPException(
            status_code=422,
            detail=f"{len(empty_ids)} chunk(s) have empty text and cannot be approved: {empty_ids[:5]}",
        )

    for chunk in chunks:
        chunk.approved = True
    await db.commit()

    # Dispatch indexing task
    task = index_document.delay(doc_id)

    logger.info(
        "chunks_approved",
        doc_id=doc_id,
        approved_count=len(chunks),
        task_id=task.id,
    )

    return {
        "doc_id":         doc_id,
        "approved_count": len(chunks),
        "task_id":        task.id,
        "status":         "indexing",
    }


# ---------------------------------------------------------------------------
# POST /documents/{doc_id}/approve-all
# ---------------------------------------------------------------------------

@router.post("/{doc_id}/approve-all", summary="Approve all chunks (skip review)")
async def approve_all(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Mark ALL chunks as approved and dispatch indexing immediately.
    Use only for trusted sources — bypasses human review.
    """
    return await approve_chunks(
        doc_id=doc_id,
        body=ApproveRequest(chunk_ids=None),
        db=db,
    )
