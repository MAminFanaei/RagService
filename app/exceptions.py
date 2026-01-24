from fastapi import status


class AppException(Exception):
    """Base exception for all app exceptions."""
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code: str = "INTERNAL_ERROR"
    
    def __init__(self, message: str = "An unexpected error occurred"):
        self.message = message
        super().__init__(message)


# ─────────────────────────────────────────────────────────────
# HTTP Exceptions
# ─────────────────────────────────────────────────────────────

class BadRequestException(AppException):
    """400 - Invalid request data."""
    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "BAD_REQUEST"
    
    def __init__(self, message: str = "Invalid request"):
        super().__init__(message)


class UnauthorizedException(AppException):
    """401 - Authentication required."""
    status_code = status.HTTP_401_UNAUTHORIZED
    error_code = "UNAUTHORIZED"
    
    def __init__(self, message: str = "Authentication required"):
        self.headers = {"WWW-Authenticate": "Bearer"}
        super().__init__(message)


class ForbiddenException(AppException):
    """403 - Access denied."""
    status_code = status.HTTP_403_FORBIDDEN
    error_code = "FORBIDDEN"
    
    def __init__(self, message: str = "Access denied"):
        super().__init__(message)


class NotFoundException(AppException):
    """404 - Resource not found."""
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "NOT_FOUND"
    
    def __init__(self, message: str = "Resource not found"):
        super().__init__(message)

class ConflictException(AppException):
    """409 - Resource conflict (e.g., duplicate email)."""
    status_code = status.HTTP_409_CONFLICT
    error_code = "CONFLICT"
    
    def __init__(self, message: str = "Resource conflict"):
        super().__init__(message)


class RateLimitException(AppException):
    """429 - Rate limit exceeded."""
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    error_code = "RATE_LIMITED"
    
    def __init__(self, message: str = "Rate limit exceeded", retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(message)


class InternalException(AppException):
    """500 - Internal server error."""
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code = "INTERNAL_ERROR"
    
    def __init__(self, message: str = "Internal server error"):
        super().__init__(message)


class NotImplementedException(AppException):
    """501 - Feature not implemented."""
    status_code = status.HTTP_501_NOT_IMPLEMENTED
    error_code = "NOT_IMPLEMENTED"
    
    def __init__(self, message: str = "Feature not implemented"):
        super().__init__(message)


# ─────────────────────────────────────────────────────────────
# Service-Specific Exceptions (non-fatal, logged only)
# ─────────────────────────────────────────────────────────────

class ServiceWarning(Exception):
    """Base for non-fatal service issues (logged, not raised to client)."""
    pass


class RedisWarning(ServiceWarning):
    """Redis operation failed but app continues."""
    pass


class ElasticsearchWarning(ServiceWarning):
    """Elasticsearch operation failed but app continues."""
    pass

# Add this exception:

class InputTooLongException(BadRequestException):
    """User input exceeds maximum length."""
    error_code = "INPUT_TOO_LONG"
    
    def __init__(
        self, 
        message: str = "Input exceeds maximum length",
        max_length: int = None,
        actual_length: int = None
    ):
        self.max_length = max_length
        self.actual_length = actual_length
        if max_length and actual_length:
            message = f"Input too long: {actual_length} characters (max: {max_length})"
        super().__init__(message)