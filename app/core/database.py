# app/core/database.py
"""
Database configuration with async support.

Uses sync SQLAlchemy with executor wrapper for non-blocking operations.
This approach is production-proven and requires minimal code changes.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Generator, AsyncGenerator, Callable, TypeVar
from functools import wraps

from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool
import redis.asyncio as aioredis
import structlog

from app.config import settings

logger = structlog.get_logger()

# =============================================================================
# THREAD POOL FOR DB OPERATIONS
# =============================================================================

# Dedicated thread pool for DB operations (separate from default)
_db_executor = ThreadPoolExecutor(
    max_workers=settings.DB_POOL_SIZE,
    thread_name_prefix="db_worker"
)

# =============================================================================
# MYSQL DATABASE
# =============================================================================

engine = create_engine(
    settings.DATABASE_URL,
    poolclass=QueuePool,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW, # max connection allowed
    pool_recycle=3600,
    pool_timeout=30,
    echo=settings.DB_ECHO
)

# Log pool events in debug mode
if settings.DEBUG:
    @event.listens_for(engine, "checkout")
    def receive_checkout(dbapi_connection, connection_record, connection_proxy):
        logger.debug("DB connection checkout", pool_size=engine.pool.size())
    
    @event.listens_for(engine, "checkin")
    def receive_checkin(dbapi_connection, connection_record):
        logger.debug("DB connection checkin", pool_size=engine.pool.size())

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# =============================================================================
# SYNC DB ACCESS (for FastAPI Depends)
# =============================================================================

def get_db() -> Generator[Session, None, None]:
    """
    Get database session (sync generator for FastAPI Depends).
    
    Used in FastAPI dependency injection.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =============================================================================
# ASYNC DB WRAPPER
# =============================================================================

T = TypeVar('T')


async def run_sync(func: Callable[..., T], *args, **kwargs) -> T:
    """
    Run a sync function in the DB thread pool.
    
    This prevents blocking the event loop while waiting for DB operations.
    
    Usage:
        result = await run_sync(db.query(User).filter(...).first)
        # or
        result = await run_sync(lambda: db.query(User).all())
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _db_executor,
        lambda: func(*args, **kwargs)
    )


async def run_sync_with_session(func: Callable[[Session], T]) -> T:
    """
    Run a sync function with a fresh session in the thread pool.
    
    Handles session lifecycle automatically.
    
    Usage:
        users = await run_sync_with_session(lambda db: db.query(User).all())
    """
    def _execute():
        db = SessionLocal()
        try:
            return func(db)
        finally:
            db.close()
    
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_db_executor, _execute)


@asynccontextmanager
async def async_session() -> AsyncGenerator[Session, None]:
    """
    Async context manager for database session.
    
    Note: Operations inside still need to use run_sync() or be quick.
    
    Usage:
        async with async_session() as db:
            result = await run_sync(lambda: db.query(User).first())
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        await run_sync(db.close)


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

def init_db():
    """Initialize database tables."""
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables initialized")


async def close_db():
    """Close database connections and thread pool."""
    _db_executor.shutdown(wait=True)
    engine.dispose()
    logger.info("Database connections closed")


async def cleanup_all():
    """Cleanup all connections (call on shutdown)."""
    await close_redis()
    await close_db()


# =============================================================================
# HEALTH CHECK
# =============================================================================

async def check_db_health() -> dict:
    """Check database health."""
    try:
        result = await run_sync_with_session(
            lambda db: db.execute("SELECT 1").scalar()
        )
        return {
            "status": "healthy",
            "pool_size": engine.pool.size(),
            "checked_out": engine.pool.checkedout(),
            "overflow": engine.pool.overflow()
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