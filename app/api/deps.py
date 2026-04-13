# app/api/deps.py
"""
FastAPI Dependencies - Async Version

All dependencies are now fully async with AsyncSession.
"""

from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from app.core.database import get_db, get_redis
from app.core.security import decode_token, is_token_blacklisted
from app.middleware.exceptions import ForbiddenException, NotFoundException, UnauthorizedException
from app.services.user_service import UserService
from app.models.user import User

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis)
) -> User:
    """
    Get current authenticated user from JWT token.
    
    Fully async - no blocking calls.
    """
    token = credentials.credentials
    # Decode and validate token
    payload = decode_token(token)

    # Check if token is blacklisted (logged out)
    if await is_token_blacklisted(redis_client, token):
        raise UnauthorizedException("Token has been revoked")
    
    if not payload:
        raise UnauthorizedException("Invalid authentication credentials")
    
    if payload.get("type") != "access":
        raise UnauthorizedException("Invalid token type")
    
    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedException("Invalid token payload")
    
    # Async user lookup
    user = await UserService.get_by_id(db, user_id)
    if not user:
        raise NotFoundException("User not found")
    
    if not user.is_active:
        raise ForbiddenException("User account is deleted")
    
    return user


async def get_current_admin_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """Ensure current user is an admin."""
    if not current_user.is_admin:
        raise ForbiddenException("Admin access required")
    return current_user


async def get_redis_client() -> aioredis.Redis:
    """Get Redis client for dependency injection."""
    return await get_redis()