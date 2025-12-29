# app/core/security.py
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from jose import JWTError, jwt
from passlib.context import CryptContext
from authlib.integrations.starlette_client import OAuth
import redis.asyncio as aioredis

from app.config import settings

# Password hashing
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# Token blacklist settings
TOKEN_BLACKLIST_PREFIX = "token_blacklist:"

# OAuth setup
oauth = OAuth()

# Google OAuth
if settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET:
    oauth.register(
        name='google',
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'}
    )

# GitHub OAuth
if settings.GITHUB_CLIENT_ID and settings.GITHUB_CLIENT_SECRET:
    oauth.register(
        name='github',
        client_id=settings.GITHUB_CLIENT_ID,
        client_secret=settings.GITHUB_CLIENT_SECRET,
        access_token_url='https://github.com/login/oauth/access_token',
        access_token_params=None,
        authorize_url='https://github.com/login/oauth/authorize',
        authorize_params=None,
        api_base_url='https://api.github.com/',
        client_kwargs={'scope': 'user:email'}
    )


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password"""
    return pwd_context.hash(password)


def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def create_refresh_token(data: Dict[str, Any]) -> str:
    """Create JWT refresh token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and verify JWT token"""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        return None


def create_token_pair(user_id: str, email: str, is_admin: bool = False) -> Dict[str, str]:
    """Create access and refresh token pair"""
    token_data = {
        "sub": user_id,
        "email": email,
        "is_admin": is_admin
    }
    
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }


# =============================================================================
# TOKEN BLACKLIST FUNCTIONS (NEW)
# =============================================================================

def get_token_jti(token: str) -> Optional[str]:
    """
    Get a unique identifier for a token.
    Uses hash of the token since JTI claim isn't present.
    """
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()[:32]


def get_token_remaining_ttl(payload: Dict[str, Any]) -> int:
    """
    Calculate remaining TTL for a token based on its expiration.
    Returns TTL in seconds.
    """
    exp = payload.get("exp")
    if not exp:
        return 0
    
    # exp is a Unix timestamp
    expiration = datetime.utcfromtimestamp(exp)
    now = datetime.utcnow()
    
    remaining = (expiration - now).total_seconds()
    return max(int(remaining), 0)


async def blacklist_token(
    redis_client: aioredis.Redis,
    token: str,
    payload: Dict[str, Any]
) -> bool:
    """
    Add a token to the blacklist.
    
    Args:
        redis_client: Redis client
        token: The JWT token string
        payload: Decoded token payload
        
    Returns:
        True if successfully blacklisted
    """
    try:
        token_id = get_token_jti(token)
        ttl = get_token_remaining_ttl(payload)
        
        if ttl <= 0:
            # Token already expired, no need to blacklist
            return True
        
        key = f"{TOKEN_BLACKLIST_PREFIX}{token_id}"
        
        # Store with TTL = remaining token lifetime
        # Value can be user_id for debugging, or just "1"
        await redis_client.set(key, payload.get("sub", "1"), ex=ttl)
        
        return True
    except Exception as e:
        print(f"⚠️ Failed to blacklist token: {e}")
        return False


async def is_token_blacklisted(
    redis_client: aioredis.Redis,
    token: str
) -> bool:
    """
    Check if a token is blacklisted.
    
    Args:
        redis_client: Redis client
        token: The JWT token string
        
    Returns:
        True if token is blacklisted
    """
    try:
        token_id = get_token_jti(token)
        key = f"{TOKEN_BLACKLIST_PREFIX}{token_id}"
        
        result = await redis_client.exists(key)
        return result > 0
    except Exception as e:
        print(f"⚠️ Failed to check token blacklist: {e}")
        # Fail open or closed? For security, fail closed (treat as blacklisted)
        # But this could lock out users if Redis is down
        # Choose based on your requirements:
        return False  # Fail open - allow access if Redis is down