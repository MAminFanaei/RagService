"""
Payment Service Health Check Router.

Provides health and readiness endpoints for monitoring.
"""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import redis.asyncio as aioredis
import structlog

from app.core.database import get_db, get_redis
from app.payment.config import payment_settings

logger = structlog.get_logger()

router = APIRouter(tags=["Payment Health"])


@router.get("/health")
async def health_check(
    db: AsyncSession = Depends(get_db),
):
    """
    Payment service health check.
    
    Checks:
    - Database connectivity
    - Redis connectivity  
    - SEP configuration loaded
    """
    health = {
        "service": "payment",
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {}
    }
    
    all_healthy = True
    
    # Check database
    try:
        await db.execute(text("SELECT 1"))
        health["checks"]["database"] = {"status": "healthy"}
    except Exception as e:
        health["checks"]["database"] = {"status": "unhealthy", "error": str(e)}
        all_healthy = False
    
    # Check Redis
    try:
        redis_client = await get_redis()
        await redis_client.ping()
        health["checks"]["redis"] = {"status": "healthy"}
    except Exception as e:
        health["checks"]["redis"] = {"status": "unhealthy", "error": str(e)}
        all_healthy = False
    
    # Check SEP config
    try:
        if payment_settings.SEP_TERMINAL_ID and payment_settings.SEP_TERMINAL_ID != "0000":
            health["checks"]["sep_config"] = {"status": "configured", "terminal": payment_settings.SEP_TERMINAL_ID}
        else:
            health["checks"]["sep_config"] = {"status": "not_configured", "note": "Using default terminal ID 0000"}
    except Exception as e:
        health["checks"]["sep_config"] = {"status": "error", "error": str(e)}
        all_healthy = False
    
    if not all_healthy:
        health["status"] = "degraded"
    
    return health
