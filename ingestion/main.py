"""
Ingestion microservice entry point.

Runs on port 8001 (separate from main app on port 8000).
Nginx routes /ingestion/* → localhost:8001.

root_path="/ingestion" makes OpenAPI docs work correctly behind the
Nginx prefix — Swagger UI will use /ingestion/docs, /ingestion/openapi.json.

Health endpoint checks DB, ES, and Redis connectivity so infra monitors
can detect partial outages.
"""

import logging
import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ingestion.database import AsyncSessionLocal
from ingestion.config import settings

# --------------------------------------------------------------------------- #
# Logging setup (structured JSON in prod, pretty in dev)                       #
# --------------------------------------------------------------------------- #

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer() if settings.DEBUG
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.DEBUG if settings.DEBUG else logging.INFO
    ),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger(__name__)

# --------------------------------------------------------------------------- #
# FastAPI app                                                                  #
# --------------------------------------------------------------------------- #

app = FastAPI(
    title="RAG Ingestion Service",
    description=(
        "Standalone microservice for document ingestion: "
        "upload → parse → OCR → chunk → embed → index into Elasticsearch."
    ),
    version="1.0.0",
    root_path="/ingestion",     # Nginx strips this prefix
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------------------------------- #
# Routers                                                                      #
# --------------------------------------------------------------------------- #

from ingestion.api.upload    import router as upload_router
from ingestion.api.review    import router as review_router
from ingestion.api.documents import router as documents_router
from ingestion.api.tasks     import router as tasks_router

# Upload: POST /documents, POST /documents/bulk
app.include_router(upload_router,    prefix="/documents", tags=["upload"])

# Review: GET|PATCH|DELETE /documents/{id}/chunks, POST /documents/{id}/approve*
app.include_router(review_router,    prefix="/documents", tags=["review"])

# Documents: GET|DELETE|PATCH /documents/{id}, POST /documents/{id}/reindex, GET /metrics
# Note: /metrics must be registered BEFORE /{doc_id} to avoid being swallowed
# by the path parameter. FastAPI routes are matched in registration order.
app.include_router(documents_router, prefix="/documents", tags=["documents"])

# Tasks: GET /tasks/{task_id}
app.include_router(tasks_router,     prefix="/tasks",     tags=["tasks"])

# --------------------------------------------------------------------------- #
# Health checks                                                                #
# --------------------------------------------------------------------------- #

async def _check_db() -> str:
    """Ping the DB by running a trivial query."""
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:
        log.error("health.db failed", error=str(exc))
        return f"error: {exc}"


async def _check_es() -> str:
    """Ping Elasticsearch cluster health endpoint."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3) as client:
            url = settings.elasticsearch_url.rstrip("/") + "/_cluster/health"
            kwargs: dict = {}
            if settings.ELASTICSEARCH_USERNAME:
                kwargs["auth"] = (
                    settings.ELASTICSEARCH_USERNAME,
                    settings.ELASTICSEARCH_PASSWORD,
                )
            r = await client.get(url, **kwargs)
            r.raise_for_status()
        return "ok"
    except Exception as exc:
        log.error("health.es failed", error=str(exc))
        return f"error: {exc}"


async def _check_redis() -> str:
    """Ping Redis with a PING command."""
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(settings.REDIS_URL, socket_timeout=3)
        await client.ping()
        await client.aclose()
        return "ok"
    except Exception as exc:
        log.error("health.redis failed", error=str(exc))
        return f"error: {exc}"


@app.get("/health", tags=["ops"])
async def health() -> dict:
    """
    Liveness + readiness check.

    Returns individual status for DB, Elasticsearch, and Redis so
    monitoring can pinpoint which dependency is degraded.
    """
    db_status    = await _check_db()
    es_status    = await _check_es()
    redis_status = await _check_redis()

    all_ok = all(s == "ok" for s in (db_status, es_status, redis_status))

    return {
        "status":  "healthy" if all_ok else "degraded",
        "version": "1.0.0",
        "checks": {
            "db":    db_status,
            "es":    es_status,
            "redis": redis_status,
        },
    }


@app.get("/", tags=["ops"])
async def root() -> dict:
    return {"service": "RAG Ingestion Service", "version": "1.0.0"}


# --------------------------------------------------------------------------- #
# Dev runner                                                                   #
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    uvicorn.run(
        "ingestion.main:app",
        host=settings.INGESTION_HOST,
        port=settings.INGESTION_PORT,
        reload=settings.DEBUG,
        log_level="debug" if settings.DEBUG else "info",
    )
