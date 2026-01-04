from typing import Optional
import redis.asyncio as aioredis
from datetime import datetime, timedelta, timezone

from app.config import settings


class RateLimitService:
    """Rate limiting service using Redis"""
    
    @staticmethod
    async def check_rate_limit(
        redis: aioredis.Redis,
        user_id: str,
        limit_per_minute: int,
        key_prefix: str = "rate_limit"
    ) -> tuple[bool, int]:
        """
        Check if user has exceeded rate limit
        
        Returns:
            (allowed: bool, remaining: int)
        """
        key = f"{key_prefix}:{user_id}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}"
        
        try:
            # Increment counter
            count = await redis.incr(key)
            
            # Set expiry on first increment
            if count == 1:
                await redis.expire(key, 60)  # Expire after 1 minute
            
            remaining = max(0, limit_per_minute - count)
            allowed = count <= limit_per_minute
            
            return allowed, remaining
        except Exception as e:
            # If Redis fails, allow the request (fail open)
            print(f"Rate limit check failed: {e}")
            return True, limit_per_minute
    
    @staticmethod
    async def check_daily_quota(
        redis: aioredis.Redis,
        user_id: str,
        max_per_day: int
    ) -> tuple[bool, int]:
        """
        Check if user has exceeded daily quota
        
        Returns:
            (allowed: bool, remaining: int)
        """
        key = f"daily_quota:{user_id}:{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        
        try:
            # Increment counter
            count = await redis.incr(key)
            
            # Set expiry on first increment
            if count == 1:
                await redis.expire(key, 86400)  # Expire after 24 hours
            
            remaining = max(0, max_per_day - count)
            allowed = count <= max_per_day
            
            return allowed, remaining
        except Exception as e:
            print(f"Daily quota check failed: {e}")
            return True, max_per_day
    
    @staticmethod
    async def get_user_limits(user) -> tuple[int, int]:
        """
        Get user's rate limits (per minute, per day)
        
        Returns:
            (rate_per_minute, quota_per_day)
        """
        rate_per_minute = user.rate_limit_per_minute or None
        quota_per_day = user.max_messages_per_day or None
        
        return rate_per_minute, quota_per_day