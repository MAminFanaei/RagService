"""
Redis-based Distributed Lock for Payment Service.

Prevents concurrent processing of the same transaction across
multiple workers/processes. Uses the provided Redis client directly.

Usage:
    from app.payment.core.locker import acquire_lock, callback_lock_key

    async with acquire_lock(redis_client, callback_lock_key("ref_num_123")):
        # ... process payment ...
        # Lock auto-released on exit
    # If lock cannot be acquired, raises LockAcquisitionException
"""

import uuid
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

import structlog
import redis.asyncio as aioredis

from app.payment.config import payment_settings
from app.payment.core.constants import LockPrefix

logger = structlog.get_logger()


# ─────────────────────────────────────────────────────────────
# Standalone acquire_lock — the primary interface
# ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def acquire_lock(
    redis_client: aioredis.Redis,
    resource: str,
    ttl: Optional[int] = None,
    timeout: Optional[int] = None,
):
    """
    Async context manager for acquiring a distributed lock.

    This is the primary interface used by callback router and ReverseService.
    It uses the provided Redis client directly.

    Usage:
        async with acquire_lock(redis_client, callback_lock_key("abc123")):
            # ... process callback ...
            # Lock auto-released on exit
        # If lock cannot be acquired, raises LockAcquisitionException

    Args:
        redis_client: Redis client instance (from Depends(get_redis)).
        resource: Resource identifier to lock (from helper functions below).
        ttl: Lock TTL in seconds (default: PAYMENT_LOCK_TTL from config).
        timeout: Max wait time (default: PAYMENT_LOCK_TIMEOUT from config).

    Raises:
        LockAcquisitionException: If lock cannot be acquired within timeout.
    """
    from app.payment.exceptions import LockAcquisitionException

    lock_ttl = ttl or payment_settings.PAYMENT_LOCK_TTL
    lock_timeout = timeout if timeout is not None else payment_settings.PAYMENT_LOCK_TIMEOUT
    key = f"lock:{resource}"
    lock_value = f"worker:{uuid.uuid4().hex[:16]}"

    # Try to acquire
    acquired = await redis_client.set(
        key,
        lock_value,
        nx=True,
        ex=lock_ttl,
    )

    if not acquired and lock_timeout > 0:
        # Retry with exponential backoff
        elapsed = 0.0
        delay = 0.1

        while elapsed < lock_timeout:
            await asyncio.sleep(delay)
            elapsed += delay

            acquired = await redis_client.set(
                key,
                lock_value,
                nx=True,
                ex=lock_ttl,
            )

            if acquired:
                break

            delay = min(delay * 2, 2.0)

    if not acquired:
        logger.warning(
            "acquire_lock_failed",
            resource=resource,
            timeout=lock_timeout,
        )
        raise LockAcquisitionException(lock_key=resource)

    logger.info(
        "acquire_lock_success",
        resource=resource,
        ttl=lock_ttl,
    )

    try:
        yield
    finally:
        # Atomic unlock — only delete if we still own it
        lua_unlock = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        end
        return 0
        """
        try:
            await redis_client.eval(lua_unlock, 1, key, lock_value)
            logger.info("acquire_lock_released", resource=resource)
        except Exception as e:
            logger.error(
                "acquire_lock_release_error",
                resource=resource,
                error=str(e),
            )


# ─────────────────────────────────────────────────────────────
# Helper functions for common lock patterns
#
# These generate the RESOURCE identifier (without "lock:" prefix).
# acquire_lock() adds "lock:" automatically.
#
# Example: callback_lock_key("abc") → "payment:callback:abc"
#          → acquire_lock adds → Redis key: "lock:payment:callback:abc"
# ─────────────────────────────────────────────────────────────

def payment_lock_key(ref_num: str) -> str:
    """Generate lock key for payment processing (by RefNum)."""
    return f"{LockPrefix.PAYMENT_REFNUM}{ref_num}"


def callback_lock_key(ref_num: str) -> str:
    """Generate lock key for callback processing."""
    return f"{LockPrefix.PAYMENT_CALLBACK}{ref_num}"


def reverse_lock_key(payment_id: str) -> str:
    """Generate lock key for reverse processing."""
    return f"{LockPrefix.REVERSE}{payment_id}"


def wallet_lock_key(user_id: str) -> str:
    """Generate lock key for wallet operations."""
    return f"{LockPrefix.WALLET}{user_id}"


def discount_lock_key(code: str) -> str:
    """Generate lock key for discount operations."""
    return f"{LockPrefix.DISCOUNT}{code}"


# ─────────────────────────────────────────────────────────────
# Diagnostic functions (for health checks and admin debugging)
# ─────────────────────────────────────────────────────────────

async def is_locked(redis_client: aioredis.Redis, resource: str) -> bool:
    """
    Check if a resource is currently locked (non-blocking).
    
    Useful for health checks and admin diagnostics.
    Does NOT acquire the lock — just checks if it exists.
    
    Args:
        redis_client: Redis client instance.
        resource: Resource identifier (same format as helper functions return).
    
    Returns:
        True if the resource is locked, False otherwise.
    """
    key = f"lock:{resource}"
    return await redis_client.exists(key) == 1


async def get_lock_ttl(redis_client: aioredis.Redis, resource: str) -> int:
    """
    Get remaining TTL for a lock in seconds.
    
    Useful for debugging stuck locks.
    
    Args:
        redis_client: Redis client instance.
        resource: Resource identifier.
    
    Returns:
        Remaining TTL in seconds.
        -1 if key exists but has no TTL (should not happen with acquire_lock).
        -2 if key does not exist (not locked).
    """
    key = f"lock:{resource}"
    return await redis_client.ttl(key)
