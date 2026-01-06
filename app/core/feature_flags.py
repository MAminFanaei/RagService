# app/core/feature_flags.py
"""
Feature flags and kill switches for endpoints.
"""

from functools import wraps
from typing import Callable, Optional
from fastapi import status

from app.config import settings
from app.exceptions import NotImplementedException


def require_feature(
    flag_name: str,
    disabled_message: Optional[str] = None,
    status_code: int = status.HTTP_503_SERVICE_UNAVAILABLE
):
    """
    Decorator to disable endpoints based on feature flags.
    
    Usage:
        @router.post("/register")
        @require_feature("ENABLE_REGISTRATION")
        async def register(...):
            ...
    
    Args:
        flag_name: Name of the setting (e.g., "ENABLE_REGISTRATION")
        disabled_message: Custom message when disabled
        status_code: HTTP status code when disabled (default: 503)
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Get flag value from settings
            is_enabled = getattr(settings, flag_name, True)
            
            if not is_enabled:
                # Try to get custom message from settings
                message = disabled_message or "This feature is temporarily disabled"
                
                raise NotImplementedException(message)
            
            return await func(*args, **kwargs)
        
        return wrapper
    return decorator


# Convenience decorators for common features
def require_registration(func: Callable):
    """Shortcut for @require_feature("ENABLE_REGISTRATION")"""
    return require_feature("ENABLE_REGISTRATION")(func)


def require_oauth(func: Callable):
    """Shortcut for @require_feature("ENABLE_OAUTH_LOGIN")"""
    return require_feature("ENABLE_OAUTH_LOGIN")(func)