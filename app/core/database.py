# app/core/database.py
"""
Async Database Configuration for SQLAlchemy 2.0

"""

from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
    AsyncEngine
)
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.pool import QueuePool, NullPool
import redis.asyncio as aioredis
import structlog

from app.config import settings

logger = structlog.get_logger()

# =============================================================================
# DATABASE URLs
# =============================================================================

# Convert sync URL to async URL
# mysql+pymysql://user:pass@host/db -> mysql+asyncmy://user:pass@host/db
ASYNC_DATABASE_URL = settings.DATABASE_URL.replace(
    "mysql+pymysql://", 
    "mysql+asyncmy://"
)

# Keep sync URL for Alembic migrations
SYNC_DATABASE_URL = settings.DATABASE_URL

# =============================================================================
# ASYNC ENGINE (for application)
# =============================================================================

async_engine: AsyncEngine = create_async_engine(
    ASYNC_DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=settings.DB_ECHO,
    # For async, we need to be careful with pool
    pool_timeout=30,
)

# Async session factory
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Important: prevents lazy loading issues
    autocommit=False,
    autoflush=False,
)

# =============================================================================
# SYNC ENGINE (for Alembic migrations only)
# =============================================================================

sync_engine = create_engine(
    SYNC_DATABASE_URL,
    poolclass=QueuePool,
    pool_pre_ping=True,
    pool_size=5,  # Smaller pool for migrations
    max_overflow=0,
    echo=settings.DB_ECHO
)

SyncSessionLocal = sessionmaker(
    autocommit=False, 
    autoflush=False, 
    bind=sync_engine
)

# =============================================================================
# BASE MODEL
# =============================================================================

Base = declarative_base()

# =============================================================================
# ASYNC DATABASE DEPENDENCY (for FastAPI)
# =============================================================================

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Async database session dependency for FastAPI.
    
    Usage:
        @router.get("/")
        async def endpoint(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(User))
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# =============================================================================
# REDIS CONNECTION
# =============================================================================

_redis_client: aioredis.Redis = None


async def get_redis() -> aioredis.Redis:
    """Get Redis connection (async)."""
    global _redis_client
    if _redis_client is None:
        _redis_client = await aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=settings.REDIS_MAX_CONNECTIONS
        )
        logger.info("Redis connected", url=settings.REDIS_URL[:20] + "...")
    return _redis_client


async def close_redis():
    """Close Redis connection."""
    global _redis_client
    if _redis_client:
        await _redis_client.close()
        _redis_client = None
        logger.info("Redis connection closed")


# =============================================================================
# INITIALIZATION & CLEANUP
# =============================================================================

def init_db_sync():
    """
    Initialize database tables (sync - for startup).
    
    Uses sync engine because this runs before the async event loop
    is fully operational during FastAPI lifespan startup.
    """
    Base.metadata.create_all(bind=sync_engine)
    logger.info("Database tables initialized (sync)")


async def close_db():
    """Close async database connections."""
    await async_engine.dispose()
    logger.info("Async database connections closed")


async def cleanup_all():
    """Cleanup all connections (call on shutdown)."""
    await close_redis()
    await close_db()


# =============================================================================
# HEALTH CHECKS
# =============================================================================

async def check_db_health() -> dict:
    """Check database health using async connection."""
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("SELECT 1"))
            result.scalar()
        
        return {
            "status": "healthy",
            "driver": "asyncmy",
            "pool_size": settings.DB_POOL_SIZE,
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


async def check_redis_health() -> dict:
    """Check Redis health."""
    try:
        redis = await get_redis()
        await redis.ping()
        info = await redis.info("clients")
        return {
            "status": "healthy",
            "connected_clients": info.get("connected_clients", "unknown")
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}