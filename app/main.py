# app/main.py
"""
FastAPI Application - Async Version

Uses async database sessions for startup operations.
"""

import multiprocessing
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from contextlib import asynccontextmanager
import structlog
import logging
import sys

from app.core.rag_engine import create_rag_engine
from app.config import settings
from app.core.database import init_db_sync, AsyncSessionLocal, cleanup_all
from app.api.v1 import auth, chats, admin
from app.middleware.error_handler import setup_exception_handlers
from app.services.user_service import UserService
from app.models.user import User, AuthProvider
from app.core.security import get_password_hash
from app.middleware.process_timing import TimingMiddleware

multiprocessing.set_start_method('spawn', force=True)  # Required for CUDA compatibility
logging.basicConfig(
    format="%(message)s",
    stream=sys.stdout,
    level=logging.INFO,
)
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
        structlog.dev.ConsoleRenderer()
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup
    logger.info("Starting RAG Service", version=settings.APP_VERSION)
    
    # Initialize database tables (sync - before event loop fully running)
    init_db_sync()
    logger.info("MySQL initialized")
    
    # Initialize RAG engine
    app.state.rag_engine = create_rag_engine()
    logger.info("RAG engine initialized", status=app.state.rag_engine.get_stats())
    
    # Create admin user if not exists - using async session
    async with AsyncSessionLocal() as db:
        try:
            admin_user = await UserService.get_by_email(db, settings.ADMIN_EMAIL)
            if not admin_user:
                admin_user = User(
                    email=settings.ADMIN_EMAIL,
                    username="admin",
                    hashed_password=get_password_hash(settings.ADMIN_PASSWORD),
                    auth_provider=AuthProvider.LOCAL,
                    is_admin=True,
                    is_active=True,
                    is_verified=True,
                    full_name="System Administrator"
                )
                db.add(admin_user)
                await db.commit()
                logger.info("Admin user created", email=settings.ADMIN_EMAIL)
        except Exception as e:
            logger.error("Failed to create admin user", error=str(e))
    
    yield
    
    # Shutdown
    logger.info("Shutting down RAG Service")
    await cleanup_all()


# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None
)

if settings.PROCESS_TIMING_ENABLE :
    app.add_middleware(TimingMiddleware) # shows process timing
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom exception handlers
setup_exception_handlers(app)

# Include routers
app.include_router(auth.router, prefix="/api/v1")
app.include_router(chats.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": f"{settings.APP_NAME} API",
        "version": settings.APP_VERSION,
    }