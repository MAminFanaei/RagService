"""
Tasks API — poll Celery task state.

GET /tasks/{task_id}
    Returns: {task_id, state, progress, result, error}

Celery states:
  PENDING  → task queued but not started (or unknown task_id)
  STARTED  → worker picked it up
  SUCCESS  → completed; result contains return value
  FAILURE  → failed; error contains exception info
  RETRY    → being retried
  REVOKED  → cancelled
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException

from ingestion.tasks.worker import celery_app

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.get("/{task_id}", summary="Poll Celery task status")
async def get_task_status(task_id: str):
    """
    Poll the status of a Celery task.

    Args:
        task_id: The task ID returned by the upload or approve endpoints.

    Returns:
        Task state, result (on success), and error message (on failure).
    """
    try:
        task = celery_app.AsyncResult(task_id)
        state = task.state

        response: dict = {
            "task_id": task_id,
            "state":   state,
            "result":  None,
            "error":   None,
        }

        if state == "SUCCESS":
            response["result"] = task.result

        elif state == "FAILURE":
            exc = task.result  # On failure, result holds the exception
            response["error"] = str(exc) if exc else "Unknown error"

        elif state == "RETRY":
            exc = task.result
            response["error"] = f"Retrying: {exc}" if exc else "Retrying"

        return response

    except Exception as exc:
        logger.error("task_status_check_failed", task_id=task_id, error=str(exc))
        raise HTTPException(
            status_code=500,
            detail=f"Could not retrieve task status: {exc}",
        )
