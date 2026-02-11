# app/core/security.py
"""
Security utilities with async password hashing.

Argon2 is CPU-intensive (~300-500ms per hash).
Async versions run in a thread pool to not block the event loop.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from jose import JWTError, jwt
from passlib.context import CryptContext
from authlib.integrations.starlette_client import OAuth
import redis.asyncio as aioredis
import structlog

from app.config import settings

logger = structlog.get_logger()

# =============================================================================
# PASSWORD HASHING SETUP
# =============================================================================

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# Single thread pool for CPU-bound password operations
# 4 workers is enough - password hashing is memory-hard, not parallelizable
_password_executor = ThreadPoolExecutor(
    max_workers=settings.PASSWORD_HASH_WORKERS,
    thread_name_prefix="argon2"
)

# =============================================================================
# SYNC PASSWORD FUNCTIONS (for non-async contexts like Alembic, scripts)
# =============================================================================

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password - SYNC version. Blocks event loop if called in async!"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash password - SYNC version. Blocks event loop if called in async!"""
    return pwd_context.hash(password)


# =============================================================================
# ASYNC PASSWORD FUNCTIONS (use these in FastAPI endpoints!)
# =============================================================================

async def verify_password_async(plain_password: str, hashed_password: str) -> bool:
    """
    Verify password - ASYNC version.
    
    Runs Argon2 verification in thread pool to not block event loop.
    Use this in all async endpoints!
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _password_executor,
        pwd_context.verify,
        plain_password,
        hashed_password
    )


async def get_password_hash_async(password: str) -> str:
    """
    Hash password - ASYNC version.
    
    Runs Argon2 hashing in thread pool to not block event loop.
    Use this in all async endpoints!
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _password_executor,
        pwd_context.hash,
        password
    )


# =============================================================================
# JWT TOKEN FUNCTIONS (fast, no async needed)
# =============================================================================

def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(data: Dict[str, Any]) -> str:
    """Create JWT refresh token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and verify JWT token."""
    if not token:
        return None
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None


def create_token_pair(user_id: str, email: str = None, username: str = None, is_admin: bool = False) -> Dict[str, str]:
    """Create access and refresh token pair."""
    token_data = {"sub": user_id, "email": email, "username": username, "is_admin": is_admin}
    return {
        "access_token": create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type": "bearer"
    }


# =============================================================================
# TOKEN BLACKLIST (for logout)
# =============================================================================

TOKEN_BLACKLIST_PREFIX = "token_blacklist:"


def get_token_jti(token: str) -> str:
    """Get unique identifier for a token."""
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()[:32]


def get_token_remaining_ttl(payload: Dict[str, Any]) -> int:
    """Calculate remaining TTL for a token."""
    exp = payload.get("exp")
    if not exp:
        return 0
    expiration = datetime.fromtimestamp(exp, tz=timezone.utc)
    remaining = (expiration - datetime.now(timezone.utc)).total_seconds()
    return max(int(remaining), 0)


async def blacklist_token(redis_client: aioredis.Redis, token: str, payload: Dict[str, Any]) -> bool:
    """Add a token to the blacklist."""
    try:
        token_id = get_token_jti(token)
        ttl = get_token_remaining_ttl(payload)
        if ttl <= 0:
            return True  # Already expired
        
        key = f"{TOKEN_BLACKLIST_PREFIX}{token_id}"
        await redis_client.set(key, payload.get("sub", "1"), ex=ttl)
        return True
    except Exception as e:
        logger.warning("Failed to blacklist token", error=str(e))
        return False


async def is_token_blacklisted(redis_client: aioredis.Redis, token: str) -> bool:
    """Check if a token is blacklisted."""
    try:
        token_id = get_token_jti(token)
        key = f"{TOKEN_BLACKLIST_PREFIX}{token_id}"
        result = await redis_client.exists(key)
        return result > 0
    except Exception as e:
        logger.warning("Failed to check token blacklist", error=str(e))
        return False  # Fail open for availability


# =============================================================================
# OAUTH SETUP
# =============================================================================

oauth = OAuth()

if settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET:
    oauth.register(
        name='google',
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'}
    )

if settings.GITHUB_CLIENT_ID and settings.GITHUB_CLIENT_SECRET:
    oauth.register(
        name='github',
        client_id=settings.GITHUB_CLIENT_ID,
        client_secret=settings.GITHUB_CLIENT_SECRET,
        access_token_url='https://github.com/login/oauth/access_token',
        authorize_url='https://github.com/login/oauth/authorize',
        api_base_url='https://api.github.com/',
        client_kwargs={'scope': 'user:email'}
    )


# =============================================================================
# CLEANUP (call on app shutdown)
# =============================================================================

def cleanup_security():
    """Shutdown the password executor."""
    _password_executor.shutdown(wait=False)