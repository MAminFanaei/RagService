"""
Celery tasks for document ingestion.

Task 1: process_document(doc_id)
    PENDING → PROCESSING → REVIEW
    Parses file, OCRs image pages, normalizes, deduplicates, chunks,
    saves Chunk rows to SQL (approved=False).

Task 2: index_document(doc_id)
    REVIEW → INDEXING → READY
    Loads approved chunks, embeds, indexes into ES, updates vector_id.

Both tasks:
  - Run the async pipeline via asyncio.run()
  - Retry up to 3 times with exponential back-off (60 s, 120 s, 240 s)
  - Set status=FAILED after max retries

Celery is synchronous by default; we wrap the async pipeline coroutines
with asyncio.run() so we don't need celery-pool-asyncio or gevent.
"""

from __future__ import annotations

import asyncio
import logging

import structlog
from celery import Task

from ingestion.tasks.worker import celery_app

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Base task class — shared retry logic
# ---------------------------------------------------------------------------

class _IngestionTask(Task):
    """
    Base class that sets status=FAILED in the DB after max retries.
    Subclasses must set `_stage` to "process" or "index".
    """
    abstract = True
    _stage: str = "unknown"

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Called by Celery after all retries are exhausted."""
        doc_id = args[0] if args else kwargs.get("doc_id", "unknown")
        logger.error(
            "task_failed_permanently",
            task_id=task_id,
            doc_id=doc_id,
            stage=self._stage,
            error=str(exc),
        )
        # Ensure status=FAILED is set (pipeline already sets it, this is a backstop)
        try:
            from ingestion.database import SyncSessionLocal
            from ingestion.models import Document, DocumentStatus
            from sqlalchemy import select as sql_select

            with SyncSessionLocal() as db:
                doc = db.get(Document, doc_id)
                if doc and doc.status not in (DocumentStatus.READY,):
                    doc.status = DocumentStatus.FAILED
                    doc.error_message = f"Task {task_id} failed: {exc}"
                    db.commit()
        except Exception as db_exc:
            logger.error("on_failure_db_update_failed", error=str(db_exc))


# ---------------------------------------------------------------------------
# Task 1: process_document
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    base=_IngestionTask,
    name="ingestion.process_document",
    max_retries=3,
    _stage="process",
)
def process_document(self: Task, doc_id: str) -> dict:
    """
    Parse, OCR, normalize, dedup, and chunk a document.
    Saves Chunk rows to SQL with approved=False.
    Sets document status to REVIEW on success.

    Returns:
        dict with doc_id, chunk_count, parser_used, ocr_used, status
    """
    logger.info("task_started", task="process_document", doc_id=doc_id)

    try:
        from ingestion.pipeline import run_process_pipeline
        result = asyncio.run(run_process_pipeline(doc_id))
        logger.info("task_succeeded", task="process_document", doc_id=doc_id, **result)
        return result

    except Exception as exc:
        retry_num = self.request.retries
        countdown  = 60 * (2 ** retry_num)   # 60 s, 120 s, 240 s

        logger.warning(
            "task_retrying",
            task="process_document",
            doc_id=doc_id,
            attempt=retry_num + 1,
            max_retries=self.max_retries,
            countdown_s=countdown,
            error=str(exc),
        )

        if retry_num < self.max_retries:
            raise self.retry(exc=exc, countdown=countdown)

        # Max retries reached — pipeline already set FAILED, just re-raise
        raise


# ---------------------------------------------------------------------------
# Task 2: index_document
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    base=_IngestionTask,
    name="ingestion.index_document",
    max_retries=3,
    _stage="index",
)
def index_document(self: Task, doc_id: str) -> dict:
    """
    Embed and index all approved chunks for doc_id into Elasticsearch.
    Sets document status to READY on success.

    Returns:
        dict with doc_id, indexed_count, status
    """
    logger.info("task_started", task="index_document", doc_id=doc_id)

    try:
        from ingestion.pipeline import run_index_pipeline
        result = asyncio.run(run_index_pipeline(doc_id))
        logger.info("task_succeeded", task="index_document", doc_id=doc_id, **result)
        return result

    except Exception as exc:
        retry_num = self.request.retries
        countdown  = 60 * (2 ** retry_num)

        logger.warning(
            "task_retrying",
            task="index_document",
            doc_id=doc_id,
            attempt=retry_num + 1,
            max_retries=self.max_retries,
            countdown_s=countdown,
            error=str(exc),
        )

        if retry_num < self.max_retries:
            raise self.retry(exc=exc, countdown=countdown)

        raise
