from typing import Optional
import redis.asyncio as aioredis
from datetime import datetime, timedelta, timezone

from app.config import settings


class RateLimitService:
    """Rate limiting service using Redis"""
    
    @staticmethod
    def _seconds_until_midnight_utc() -> int:
        """Calculate seconds until UTC midnight."""
        now = datetime.now(timezone.utc)
        midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return int((midnight - now).total_seconds())
    
    # ─────────────────────────────────────────────────────────
    # CHECK ONLY (no increment)
    # ─────────────────────────────────────────────────────────
    
    @staticmethod
    async def check_per_min_rate_limit(
        redis: aioredis.Redis,
        user_id: str,
        limit_per_minute: int,
        key_prefix: str = "rate_limit"
    ) -> tuple[bool, int]:
        """
        Check if user is within rate limit (WITHOUT incrementing).
        
        Returns:
            (allowed: bool, remaining: int)
        """
        key = f"{key_prefix}:{user_id}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}"
        
        try:
            current = await redis.get(key)
            count = int(current) if current else 0
            
            allowed = count < limit_per_minute  # < not <= (we haven't incremented yet)
            
            return allowed
            
        except Exception as e:
            print(f"Rate limit check failed: {e}")
            return True
    
    @staticmethod
    async def check_daily_quota(
        redis: aioredis.Redis,
        user_id: str,
        max_per_day: int
    ) -> tuple[bool, int]:
        """
        Check if user is within daily quota (WITHOUT incrementing).
        
        Returns:
            (allowed: bool, remaining: int)
        """
        key = f"daily_quota:{user_id}:{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        
        try:
            current = await redis.get(key)
            count = int(current) if current else 0
            
            remaining = max(0, max_per_day - count)
            allowed = count < max_per_day  # < not <=
            
            return allowed, remaining
            
        except Exception as e:
            print(f"Daily quota check failed: {e}")
            return True, max_per_day
    
    # ─────────────────────────────────────────────────────────
    # INCREMENT ONLY (after success)
    # ─────────────────────────────────────────────────────────
    
    @staticmethod
    async def increment_rate_limit(
        redis: aioredis.Redis,
        user_id: str,
        key_prefix: str = "rate_limit"
    ) -> int:
        """
        Increment rate limit counter AFTER successful operation.
        
        Returns:
            new count
        """
        key = f"{key_prefix}:{user_id}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}"
        
        try:
            async with redis.pipeline(transaction=True) as pipe:
                await pipe.incr(key)
                await pipe.expire(key, 60)
                results = await pipe.execute()
            
            return results[0]
            
        except Exception as e:
            print(f"Rate limit increment failed: {e}")
            return 0
    
    @staticmethod
    async def increment_daily_quota(
        redis: aioredis.Redis,
        user_id: str
    ) -> int:
        """
        Increment daily quota counter AFTER successful operation.
        
        Returns:
            new count
        """
        key = f"daily_quota:{user_id}:{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        
        try:
            ttl = RateLimitService._seconds_until_midnight_utc()
            
            async with redis.pipeline(transaction=True) as pipe:
                await pipe.incr(key)
                await pipe.expire(key, ttl)
                results = await pipe.execute()
            
            return results[0]
            
        except Exception as e:
            print(f"Daily quota increment failed: {e}")
            return 0
    
    # ─────────────────────────────────────────────────────────
    # HELPER
    # ─────────────────────────────────────────────────────────
    
    @staticmethod
    def get_user_limits(user) -> tuple[int, int]:
        """Get user's rate limits (per minute, per day)."""
        rate_per_minute = max(user.rate_limit_per_minute, settings.DEFAULT_RATE_LIMIT_PER_MINUTE) if user.rate_limit_per_minute else settings.DEFAULT_RATE_LIMIT_PER_MINUTE
        quota_per_day = max(user.max_messages_per_day ,settings.DEFAULT_MAX_MESSAGES_PER_DAY )  if user.max_messages_per_day else settings.DEFAULT_MAX_MESSAGES_PER_DAY
        
        return rate_per_minute, quota_per_day