from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from contextlib import asynccontextmanager
import structlog

from app.config import settings
from app.core.database import init_db, close_redis
from app.core.rag_engine import rag_engine
from app.api.v1 import auth, chats, admin
from app.middleware.error_handler import setup_exception_handlers

# Configure structured logging
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    # Startup
    logger.info("Starting RAG Service", version=settings.APP_VERSION)
    
    # Initialize database
    init_db()
    logger.info("Database initialized")
    
    # Initialize RAG engine (this happens in singleton constructor)
    logger.info("RAG engine initialized", status=rag_engine.get_stats())
    
    # Create admin user if not exists
    from app.core.database import SessionLocal
    from app.services.user_service import UserService
    from app.core.security import get_password_hash
    from app.models.user import User, AuthProvider
    
    db = SessionLocal()
    try:
        admin_user = UserService.get_by_email(db, settings.ADMIN_EMAIL)
        if not admin_user:
            admin_user = User(
                email=settings.ADMIN_EMAIL,
                hashed_password=get_password_hash(settings.ADMIN_PASSWORD),
                auth_provider=AuthProvider.LOCAL,
                is_admin=True,
                is_active=True,
                is_verified=True,
                full_name="System Administrator"
            )
            db.add(admin_user)
            db.commit()
            logger.info("Admin user created", email=settings.ADMIN_EMAIL)
    finally:
        db.close()
    
    yield
    
    # Shutdown
    logger.info("Shutting down RAG Service")
    await close_redis()


# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limit error handler
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Custom exception handlers
setup_exception_handlers(app)

# Include routers
app.include_router(auth.router, prefix="/api/v1")
app.include_router(chats.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    rag_stats = rag_engine.get_stats()
    
    return {
        "status": "healthy",
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "rag_engine": {
            "status": rag_stats.get("status", "unknown"),
            "documents": rag_stats.get("documents_count", 0)
        }
    }


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": f"{settings.APP_NAME} API",
        "version": settings.APP_VERSION,
        "docs": "/docs" if settings.DEBUG else "disabled",
        "health": "/health"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )