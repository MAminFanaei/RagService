"""
Celery application for the ingestion microservice.

Broker and backend both default to REDIS_URL from .env.
CELERY_BROKER_URL / CELERY_RESULT_BACKEND override if set.

Key settings:
  task_acks_late=True            → task re-queued if worker crashes mid-run
  task_reject_on_worker_lost=True→ same protection for hard crashes
  worker_prefetch_multiplier=1   → one GPU-heavy task at a time per worker
  task_serializer="json"         → human-readable, avoids pickle security issues

Import tasks at the bottom so Celery discovers them automatically
when the worker starts.
"""

from celery import Celery

from ingestion.config import get_settings

settings = get_settings()

# ---------------------------------------------------------------------------
# Broker / backend resolution
# ---------------------------------------------------------------------------

_broker  = settings.CELERY_BROKER_URL  or settings.REDIS_URL
_backend = settings.CELERY_RESULT_BACKEND or settings.REDIS_URL

# ---------------------------------------------------------------------------
# Celery app
# ---------------------------------------------------------------------------

celery_app = Celery(
    "ingestion",
    broker=_broker,
    backend=_backend,
    include=[
        "ingestion.tasks.ingestion_task",   # auto-discover tasks
    ],
)

celery_app.conf.update(
    # Reliability
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # Concurrency — one GPU task at a time; raise in .env if CPU-only
    worker_prefetch_multiplier=1,

    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Result expiry (24 h)
    result_expires=86400,

    # Timezone
    timezone="UTC",
    enable_utc=True,

    # Task time limit: 30 min hard, 25 min soft
    task_time_limit=1800,
    task_soft_time_limit=1500,
)

# ---------------------------------------------------------------------------
# Dev / test runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    celery_app.start()
