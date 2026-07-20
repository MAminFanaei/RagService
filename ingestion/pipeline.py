"""
Pipeline orchestrator — ties all ingestion steps together.

Called by Celery tasks (ingestion/tasks/ingestion_task.py).
NOT called directly by the API — the API dispatches tasks.

Two public coroutines:
  run_process_pipeline(doc_id)  → PENDING → PROCESSING → REVIEW
  run_index_pipeline(doc_id)    → REVIEW  → INDEXING   → READY

Both use the sync DB session (Celery tasks are sync; they call
asyncio.run() around these coroutines OR use the sync helpers directly).
The functions here are written as async so they can be awaited from
either an async context or via asyncio.run() in the Celery task.

Design note on issue #8 (parser_used None during select_chunker):
  We set doc.parser_used and doc.ocr_used BEFORE calling select_chunker,
  so the router can read them correctly.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any
from ingestion.storage import get_storage
import structlog
from ingestion.database import AsyncSessionLocal
from ingestion.config import get_settings
from ingestion.models import Document, DocumentStatus, Chunk
from ingestion.vector_store import get_vector_store
from ingestion.parsers.router import route_and_parse
from ingestion.parsers.base import ParsedElement
from ingestion.ocr.router import route_and_ocr
from ingestion.normalizer import normalize
from ingestion.dedup import filter_chunks
from ingestion.chunkers.router import select_chunker
logger = structlog.get_logger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _build_chunk_record(chunk_dict: dict, doc_id: str) -> Chunk:
    """
    Convert a chunk dict (from chunker output) into a Chunk ORM instance.
    Only maps fields that exist on the Chunk model.
    """
    return Chunk(
        doc_id=doc_id,
        page_number=chunk_dict.get("page_number"),
        page_range=chunk_dict.get("page_range"),
        bounding_box=chunk_dict.get("bounding_box"),
        section_title=chunk_dict.get("section_title"),
        section_path=chunk_dict.get("section_path"),
        heading_level=chunk_dict.get("heading_level"),
        element_type=chunk_dict.get("element_type", "text"),
        is_footnote=chunk_dict.get("is_footnote", False),
        is_table=chunk_dict.get("is_table", False),
        table_markdown=chunk_dict.get("table_markdown"),
        text=chunk_dict.get("text", ""),
        char_count=chunk_dict.get("char_count", len(chunk_dict.get("text", ""))),
        token_estimate=chunk_dict.get("token_estimate"),
        language=chunk_dict.get("language"),
        script_direction=chunk_dict.get("script_direction"),
        chunk_index=chunk_dict.get("chunk_index", 0),
        total_chunks=chunk_dict.get("total_chunks"),
        approved=False,
        edited_by_admin=False,
    )


# ---------------------------------------------------------------------------
# Process pipeline: file → chunks saved to SQL (status → REVIEW)
# ---------------------------------------------------------------------------

async def run_process_pipeline(doc_id: str) -> dict[str, Any]:
    """
    Full processing pipeline for one document.

    Steps:
      1.  Load Document from DB, set status=PROCESSING
      2.  Load raw bytes from storage
      3.  Route to parser → list[ParsedElement]
      4.  For any image_page elements → route to OCR
      5.  Normalize elements → list[dict]
      6.  Dedup + quality filter
      7.  Select chunker (using now-populated parser_used / ocr_used)
      8.  Chunk → final list[dict]
      9.  Save Chunk rows to SQL (approved=False)
      10. Set doc.status=REVIEW, update metadata
      11. Return summary dict

    Raises on unrecoverable errors after setting status=FAILED.
    """

    from sqlalchemy import select

    t0 = time.monotonic()

    async with AsyncSessionLocal() as db:
        # ---- 1. Load document ----------------------------------------- #
        result = await db.execute(
            select(Document).where(Document.id == doc_id)
        )
        doc: Document | None = result.scalar_one_or_none()
        if doc is None:
            raise ValueError(f"Document {doc_id} not found")

        doc.status = DocumentStatus.PROCESSING
        await db.commit()
        await db.refresh(doc)

        try:
            # ---- 2. Load file bytes ----------------------------------- #
            t_stage = time.monotonic()
            storage = get_storage()
            data = await storage.load(doc.storage_path)
            logger.info("stage_complete",
                        doc_id=doc_id, stage="storage_load",
                        duration_ms=_elapsed_ms(t_stage), status="success",
                        count=len(data))

            # ---- 3. Parse --------------------------------------------- #
            t_stage = time.monotonic()
            elements: list[ParsedElement] = await route_and_parse(
                mime_type=doc.mime_type,
                data=data,
                filename=doc.original_filename,
            )

            # Determine which parser was used (first element's parser_name)
            parser_used = elements[0].parser_name if elements else "unknown"
            doc.parser_used = parser_used
            # page count: max page_number seen
            page_numbers = [e.page_number for e in elements if e.page_number]
            if page_numbers:
                doc.total_pages = max(page_numbers)

            logger.info("stage_complete",
                        doc_id=doc_id, stage="parsing",
                        duration_ms=_elapsed_ms(t_stage), status="success",
                        count=len(elements), parser=parser_used)

            # ---- 4. OCR for image pages -------------------------------- #
            image_elements = [e for e in elements if e.element_type == "image_page"]
            text_elements  = [e for e in elements if e.element_type != "image_page"]
            ocr_used: str | None = None

            if image_elements:
                t_stage = time.monotonic()
                ocr_results: list[ParsedElement] = []
                for img_el in image_elements:
                    image_bytes = img_el.raw_metadata.get("image_bytes", b"")
                    if not image_bytes:
                        continue
                    ocr_result = await route_and_ocr(
                        image_bytes=image_bytes,
                        language_hint=img_el.language_hint,
                    )
                    ocr_used = ocr_result.engine_name
                    # If OCR returned structured elements, use them
                    if ocr_result.structured_elements:
                        ocr_results.extend(ocr_result.structured_elements)
                    else:
                        # Wrap plain text in a ParsedElement
                        ocr_results.append(ParsedElement(
                            text=ocr_result.text,
                            parser_name=ocr_result.engine_name,
                            element_type="text",
                            page_number=img_el.page_number,
                            section_path=[],
                            language_hint=ocr_result.language_detected,
                        ))

                doc.ocr_used = ocr_used
                elements = text_elements + ocr_results
                logger.info("stage_complete",
                            doc_id=doc_id, stage="ocr",
                            duration_ms=_elapsed_ms(t_stage), status="success",
                            count=len(ocr_results), engine=ocr_used)
            else:
                elements = text_elements

            # Commit parser_used / ocr_used / total_pages so select_chunker sees them
            await db.commit()
            await db.refresh(doc)

            # ---- 5. Normalize ----------------------------------------- #
            t_stage = time.monotonic()
            normalized = normalize(elements, doc)
            logger.info("stage_complete",
                        doc_id=doc_id, stage="normalization",
                        duration_ms=_elapsed_ms(t_stage), status="success",
                        count=len(normalized))

            # ---- 6. Dedup + quality filter ----------------------------- #
            t_stage = time.monotonic()
            filtered = filter_chunks(normalized)
            logger.info("stage_complete",
                        doc_id=doc_id, stage="dedup",
                        duration_ms=_elapsed_ms(t_stage), status="success",
                        count=len(filtered))

            # ---- 7. Select chunker ------------------------------------ #
            # doc.parser_used and doc.ocr_used are now set (fixes issue #8)
            chunker = select_chunker(doc, filtered)

            # ---- 8. Chunk -------------------------------------------- #
            t_stage = time.monotonic()
            final_chunks = chunker.chunk(filtered)
            logger.info("stage_complete",
                        doc_id=doc_id, stage="chunking",
                        duration_ms=_elapsed_ms(t_stage), status="success",
                        count=len(final_chunks), chunker=chunker.name)

            # ---- 9. Save Chunk rows ----------------------------------- #
            t_stage = time.monotonic()
            for chunk_dict in final_chunks:
                db.add(_build_chunk_record(chunk_dict, doc_id))

            doc.total_chunks = len(final_chunks)
            doc.status = DocumentStatus.REVIEW
            doc.processed_at = _now()

            await db.commit()
            logger.info("stage_complete",
                        doc_id=doc_id, stage="db_save",
                        duration_ms=_elapsed_ms(t_stage), status="success",
                        count=len(final_chunks))

        except Exception as exc:
            doc.status = DocumentStatus.FAILED
            doc.error_message = str(exc)
            await db.commit()
            logger.error("pipeline_failed",
                         doc_id=doc_id, stage="process",
                         error=str(exc), duration_ms=_elapsed_ms(t0))
            raise

    logger.info("pipeline_complete",
                doc_id=doc_id, stage="process",
                duration_ms=_elapsed_ms(t0),
                chunk_count=len(final_chunks))

    return {
        "doc_id":      doc_id,
        "chunk_count": len(final_chunks),
        "parser_used": parser_used,
        "ocr_used":    ocr_used,
        "status":      "REVIEW",
    }


# ---------------------------------------------------------------------------
# Index pipeline: approved chunks → ES (status → READY)
# ---------------------------------------------------------------------------

async def run_index_pipeline(doc_id: str) -> dict[str, Any]:
    """
    Embed and index all approved chunks for doc_id into Elasticsearch.

    Steps:
      1. Load Document, set status=INDEXING
      2. Load approved Chunk rows
      3. Build chunk dicts for vector_store
      4. Call vector_store.add_chunks() → list of ES _ids
      5. Update each Chunk.vector_id in SQL
      6. Set doc.status=READY
      7. Return summary

    Raises on error after setting status=FAILED.
    """
    
    from sqlalchemy import select

    t0 = time.monotonic()

    async with AsyncSessionLocal() as db:
        # ---- 1. Load document ----------------------------------------- #
        result = await db.execute(
            select(Document).where(Document.id == doc_id)
        )
        doc: Document | None = result.scalar_one_or_none()
        if doc is None:
            raise ValueError(f"Document {doc_id} not found")

        doc.status = DocumentStatus.INDEXING
        await db.commit()
        await db.refresh(doc)

        try:
            # ---- 2. Load approved chunks ------------------------------ #
            t_stage = time.monotonic()
            chunk_result = await db.execute(
                select(Chunk).where(
                    Chunk.doc_id == doc_id,
                    Chunk.approved == True,  # noqa: E712
                )
            )
            chunks: list[Chunk] = list(chunk_result.scalars().all())

            if not chunks:
                logger.warning("index_pipeline_no_chunks",
                               doc_id=doc_id,
                               message="No approved chunks found — marking READY with 0 indexed")
                doc.status = DocumentStatus.READY
                await db.commit()
                return {"doc_id": doc_id, "indexed_count": 0, "status": "READY"}

            logger.info("stage_complete",
                        doc_id=doc_id, stage="load_chunks",
                        duration_ms=_elapsed_ms(t_stage), status="success",
                        count=len(chunks))

            # ---- 3. Build chunk dicts --------------------------------- #
            # Rebuild the dict that vector_store expects (ES chunk schema)
            chunk_dicts: list[dict] = []
            for c in chunks:
                section_title = c.section_title or ""
                text = c.text or ""
                chunk_dicts.append({
                    "chunk_id":           c.id,
                    "doc_id":             c.doc_id,
                    "text":               text,
                    "section_title_text": f"{section_title} {text}".strip() if section_title else text,
                    "source_file":        doc.original_filename,
                    "doc_title":          (doc.custom or {}).get("title", doc.original_filename),
                    "page_number":        c.page_number,
                    "section_path":       c.section_path or [],
                    "section_title":      section_title or None,
                    "element_type":       c.element_type,
                    "is_table":           c.is_table,
                    "is_footnote":        c.is_footnote,
                    "table_markdown":     c.table_markdown,
                    "language":           c.language,
                    "script_direction":   c.script_direction,
                    "chunk_index":        c.chunk_index,
                    "total_chunks":       c.total_chunks,
                    "token_count":        c.token_estimate,
                    "tags":               list(doc.tags or []),
                    "ingestion_version":  doc.ingestion_version,
                })

            # ---- 4. Embed + index ------------------------------------- #
            t_stage = time.monotonic()
            vs = get_vector_store()
            es_ids = await vs.add_chunks(chunk_dicts)
            logger.info("stage_complete",
                        doc_id=doc_id, stage="embedding+indexing",
                        duration_ms=_elapsed_ms(t_stage), status="success",
                        count=len(es_ids))

            # ---- 5. Update vector_id in SQL -------------------------- #
            t_stage = time.monotonic()
            id_map = dict(zip([c.id for c in chunks], es_ids))
            for chunk in chunks:
                chunk.vector_id = id_map.get(chunk.id)
            await db.commit()
            logger.info("stage_complete",
                        doc_id=doc_id, stage="vector_id_update",
                        duration_ms=_elapsed_ms(t_stage), status="success")

            # ---- 6. Mark READY --------------------------------------- #
            doc.status = DocumentStatus.READY
            await db.commit()

        except Exception as exc:
            doc.status = DocumentStatus.FAILED
            doc.error_message = str(exc)
            await db.commit()
            logger.error("pipeline_failed",
                         doc_id=doc_id, stage="index",
                         error=str(exc), duration_ms=_elapsed_ms(t0))
            raise

    logger.info("pipeline_complete",
                doc_id=doc_id, stage="index",
                duration_ms=_elapsed_ms(t0),
                indexed_count=len(es_ids))

    return {
        "doc_id":        doc_id,
        "indexed_count": len(es_ids),
        "status":        "READY",
    }
