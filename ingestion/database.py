"""
Ingestion service — fully independent database module.

Creates its own Base, async_engine, and sync_engine from DATABASE_URL in .env.
Zero imports from app/. Can be moved to a separate repo by just changing the .env path.

- async_engine / AsyncSessionLocal / get_db()  → used by FastAPI endpoints
- sync_engine / SyncSessionLocal               → used by Celery tasks (which run
  in a sync context and call asyncio.run() themselves, OR use the sync session
  directly when running blocking code)
- Base                                         → imported by ingestion/models.py
  and registered in alembic/env.py
"""

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from ingestion.config import get_settings


# --------------------------------------------------------------------------- #
# Declarative base — ingestion-only, separate from app's Base                 #
# --------------------------------------------------------------------------- #

class Base(DeclarativeBase):
    """
    Separate SQLAlchemy declarative base for all ingestion models.

    Alembic sees this via alembic/env.py:
        from ingestion.database import Base as IngestionBase
        target_metadata = [AppBase.metadata, IngestionBase.metadata]
    """
    pass


# --------------------------------------------------------------------------- #
# Engine factories (lazy — built once on first access)                        #
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=1)
def _get_async_engine():
    s = get_settings()
    return create_async_engine(
        s.database_url,
        echo=s.DEBUG,
        pool_pre_ping=True,
        pool_recycle=3600,
    )


@lru_cache(maxsize=1)
def _get_sync_engine():
    s = get_settings()
    # Convert asyncmy/aiosqlite URL to sync equivalent for Celery / Alembic
    url = (
        s.database_url
        .replace("mysql+asyncmy://", "mysql+pymysql://")
        .replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("sqlite+aiosqlite://", "sqlite://")
    )
    return create_engine(
        url,
        echo=s.DEBUG,
        pool_pre_ping=True,
        pool_recycle=3600,
    )


# Public references — import these in other ingestion modules
@property
def async_engine():  # type: ignore[override]
    return _get_async_engine()


# --------------------------------------------------------------------------- #
# Session factories                                                            #
# --------------------------------------------------------------------------- #

def _make_async_session_factory():
    return async_sessionmaker(
        bind=_get_async_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


def _make_sync_session_factory():
    return sessionmaker(
        bind=_get_sync_engine(),
        class_=Session,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


# Module-level factories (created on first import of this module)
AsyncSessionLocal = _make_async_session_factory()
SyncSessionLocal  = _make_sync_session_factory()


# --------------------------------------------------------------------------- #
# FastAPI dependency                                                           #
# --------------------------------------------------------------------------- #

async def get_db():
    """
    Async FastAPI dependency that yields a database session.

    Usage:
        @router.get("/")
        async def endpoint(db: AsyncSession = Depends(get_db)):
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


# --------------------------------------------------------------------------- #
# Celery / sync helper                                                         #
# --------------------------------------------------------------------------- #

def get_sync_db():
    """
    Sync context manager for use in Celery tasks.

    Usage:
        with get_sync_db() as db:
            db.query(Document).filter(...).first()
    """
    return SyncSessionLocal()


# --------------------------------------------------------------------------- #
# Expose engines as module-level names for Alembic                            #
# --------------------------------------------------------------------------- #

# Alembic env.py imports these directly:
#   from ingestion.database import sync_engine, Base
sync_engine  = _get_sync_engine()
