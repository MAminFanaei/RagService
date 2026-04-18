from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
import structlog
from app.middleware.exceptions import AppException, RateLimitException
from app.payment.exceptions import AppException as PaymentAppException
from app.config import settings
logger = structlog.get_logger()


def setup_exception_handlers(app: FastAPI):
    """Setup global exception handlers"""

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
        """Handle database errors."""
        logger.error("Database error", error=str(exc), path=request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "DATABASE_ERROR",
                "message": "A database error occurred",
                "detail": str(exc) if settings.DEBUG else None,
            },
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """Catch-all for unhandled exceptions."""
        logger.error(
            "Unhandled exception",
            error=str(exc),
            error_type=type(exc).__name__,
            path=request.url.path,
            exc_info=True
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "INTERNAL_ERROR",
                "message": "An internal error occurred",
                "detail": str(exc) if settings.DEBUG else None
            }
        )

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        logger.warning(
            "App exception",
            error_code=exc.error_code,
            message=exc.message,
            path=request.url.path,
            context=getattr(exc, 'context', None),
        )

        # Collect headers from exception
        headers = getattr(exc, 'headers', None) or {}
        content = {
            "error": exc.error_code,
            "message": exc.message
        }

        if hasattr(exc, "data") and exc.data is not None:
            content["data"] = exc.data
        
        # ADD these lines - show context in debug mode
        if settings.DEBUG and hasattr(exc, "context") and exc.context:
            content["context"] = exc.context
            
        # Add Retry-After for rate limits
        if isinstance(exc, RateLimitException):
            headers["Retry-After"] = str(exc.retry_after)
            content["retry_after"] = exc.retry_after

        return JSONResponse(
            status_code=exc.status_code,
            content=content,
            headers=headers or None
        )

    # Keep payment exceptions isolated for easier future extraction.
    @app.exception_handler(PaymentAppException)
    async def payment_app_exception_handler(request: Request, exc: PaymentAppException):
        logger.warning(
            "Payment app exception",
            error_code=exc.error_code,
            message=exc.message,
            path=request.url.path,
        )

        # Collect headers from exception
        headers = getattr(exc, 'headers', None) or {}
        content = {
            "error": exc.error_code,
                "message": exc.message
        }

        if hasattr(exc, "data") and exc.data is not None:
            content["data"] = exc.data

        # If payment side ever raises same RateLimitException type.
        if isinstance(exc, RateLimitException):
            headers["Retry-After"] = str(exc.retry_after)
            content["retry_after"] = exc.retry_after

        return JSONResponse(
            status_code=exc.status_code,
            content=content,
            headers=headers or None,
        )
    
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Handle request validation errors with a stable response shape."""
        logger.warning("Validation error", path=request.url.path, errors=exc.errors())
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "detail": exc.errors() if settings.DEBUG else None,
            },
        )