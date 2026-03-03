"""
Redis-based Distributed Lock for Payment Service.

Prevents concurrent processing of the same transaction across
multiple workers/processes. Uses the existing Redis connection
from app.core.database.get_redis().

Usage:
    # Context manager with Redis client (recommended for services)
    async with acquire_lock(redis_client, "callback:abc123"):
        # ... process payment ...
        # Lock auto-released on exit

    # Using the locker singleton directly
    locker = DistributedLocker()
    async with locker.acquire("payment:refnum:abc123") as acquired:
        if not acquired:
            raise DuplicatePaymentException(...)
        # ... process payment ...

    # Manual lock/unlock
    acquired = await locker.lock("payment:refnum:abc123")
    try:
        # ... process ...
    finally:
        await locker.unlock("payment:refnum:abc123")
"""

import uuid
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

import structlog
import redis.asyncio as aioredis

from app.core.database import get_redis
from app.payment.config import payment_settings
from app.payment.core.constants import LockPrefix

logger = structlog.get_logger()


class DistributedLocker:
    """
    Redis-based distributed lock manager.
    
    Uses SET NX EX (atomic set-if-not-exists with expiry) to ensure
    only one worker processes a given transaction at a time.
    
    Features:
    - Atomic lock acquisition (SET NX EX)
    - Automatic TTL expiry (prevents deadlocks if worker crashes)
    - Owner-based unlocking (only the locker that acquired can release)
    - Retry with backoff for transient Redis issues
    - Async context manager support
    """

    def __init__(self):
        self._owner_id = str(uuid.uuid4())  # Unique ID for this instance

    def _make_key(self, resource: str) -> str:
        """Build the full Redis key for a lock."""
        return f"lock:{resource}"

    async def _get_redis(self) -> aioredis.Redis:
        """Get Redis client from shared connection pool."""
        return await get_redis()

    async def lock(
        self,
        resource: str,
        ttl: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> bool:
        """
        Attempt to acquire a distributed lock.

        Args:
            resource: The resource identifier to lock (e.g., "payment:refnum:abc123")
            ttl: Lock time-to-live in seconds. Defaults to config PAYMENT_LOCK_TTL.
                 After this time, the lock auto-expires (prevents deadlocks).
            timeout: Max seconds to wait for lock acquisition.
                     Defaults to config PAYMENT_LOCK_TIMEOUT.
                     If 0, returns immediately without waiting.

        Returns:
            True if lock was acquired, False if not (resource already locked).
        """
        lock_ttl = ttl or payment_settings.PAYMENT_LOCK_TTL
        lock_timeout = timeout if timeout is not None else payment_settings.PAYMENT_LOCK_TIMEOUT
        key = self._make_key(resource)
        lock_value = f"{self._owner_id}:{uuid.uuid4().hex[:8]}"

        redis = await self._get_redis()

        # Immediate attempt
        acquired = await redis.set(
            key,
            lock_value,
            nx=True,  # Only set if NOT exists
            ex=lock_ttl,  # Expire after TTL seconds
        )

        if acquired:
            logger.info(
                "lock_acquired",
                resource=resource,
                key=key,
                ttl=lock_ttl,
                owner=lock_value[:12],
            )
            # Store lock_value so we can verify ownership on unlock
            await redis.hset("lock:owners", key, lock_value)
            return True

        # If no timeout, fail immediately
        if lock_timeout == 0:
            logger.warning(
                "lock_not_acquired_immediate",
                resource=resource,
                key=key,
            )
            return False

        # Retry with exponential backoff up to timeout
        elapsed = 0.0
        delay = 0.1  # Start with 100ms

        while elapsed < lock_timeout:
            await asyncio.sleep(delay)
            elapsed += delay

            acquired = await redis.set(
                key,
                lock_value,
                nx=True,
                ex=lock_ttl,
            )

            if acquired:
                logger.info(
                    "lock_acquired_after_wait",
                    resource=resource,
                    key=key,
                    ttl=lock_ttl,
                    waited_seconds=round(elapsed, 2),
                    owner=lock_value[:12],
                )
                await redis.hset("lock:owners", key, lock_value)
                return True

            # Exponential backoff, capped at 2 seconds
            delay = min(delay * 2, 2.0)

        logger.warning(
            "lock_not_acquired_timeout",
            resource=resource,
            key=key,
            timeout=lock_timeout,
        )
        return False

    async def unlock(self, resource: str) -> bool:
        """
        Release a distributed lock.

        Uses a Lua script for atomic check-and-delete to ensure
        only the owner can release the lock.

        Args:
            resource: The resource identifier to unlock.

        Returns:
            True if lock was released, False if lock didn't exist
            or wasn't owned by this instance.
        """
        key = self._make_key(resource)
        redis = await self._get_redis()

        # Lua script: atomic check owner + delete
        lua_script = """
        local owner = redis.call('hget', KEYS[2], KEYS[1])
        if owner then
            local current = redis.call('get', KEYS[1])
            if current == owner then
                redis.call('del', KEYS[1])
                redis.call('hdel', KEYS[2], KEYS[1])
                return 1
            end
        end
        redis.call('hdel', KEYS[2], KEYS[1])
        return 0
        """

        try:
            result = await redis.eval(
                lua_script,
                2,  # Number of KEYS
                key,
                "lock:owners",
            )

            if result == 1:
                logger.info("lock_released", resource=resource, key=key)
                return True
            else:
                logger.warning(
                    "lock_release_failed",
                    resource=resource,
                    key=key,
                    reason="not_owner_or_expired",
                )
                return False

        except Exception as e:
            # Even if unlock fails, the TTL will eventually expire
            logger.error(
                "lock_release_error",
                resource=resource,
                key=key,
                error=str(e),
            )
            return False

    async def is_locked(self, resource: str) -> bool:
        """Check if a resource is currently locked (non-blocking)."""
        key = self._make_key(resource)
        redis = await self._get_redis()
        return await redis.exists(key) == 1

    async def get_ttl(self, resource: str) -> int:
        """Get remaining TTL for a lock in seconds. Returns -2 if key doesn't exist."""
        key = self._make_key(resource)
        redis = await self._get_redis()
        return await redis.ttl(key)

    @asynccontextmanager
    async def acquire(
        self,
        resource: str,
        ttl: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> AsyncGenerator[bool, None]:
        """
        Async context manager for lock acquisition.

        Usage:
            async with locker.acquire("payment:refnum:abc123") as acquired:
                if not acquired:
                    raise DuplicatePaymentException(...)
                # ... do work ...
            # Lock is automatically released on exit

        Args:
            resource: The resource identifier to lock.
            ttl: Lock TTL in seconds.
            timeout: Max wait time in seconds.

        Yields:
            True if lock was acquired, False otherwise.
        """
        acquired = await self.lock(resource, ttl=ttl, timeout=timeout)
        try:
            yield acquired
        finally:
            if acquired:
                await self.unlock(resource)


# ─────────────────────────────────────────────────────────────
# Standalone acquire_lock — used by services
# ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def acquire_lock(
    redis_client: aioredis.Redis,
    resource: str,
    ttl: Optional[int] = None,
    timeout: Optional[int] = None,
):
    """
    Standalone async context manager for acquiring a distributed lock.

    This is the primary interface used by PaymentService and ReverseService.
    It uses the provided Redis client directly (no need for the singleton).

    Usage:
        async with acquire_lock(redis_client, "callback:abc123"):
            # ... process callback ...
            # Lock auto-released on exit
        # If lock cannot be acquired, raises LockAcquisitionException

    Args:
        redis_client: Redis client instance (from Depends(get_redis)).
        resource: Resource identifier to lock.
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
        raise LockAcquisitionException(resource=resource)

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


# Singleton instance — import this in services if using class-based approach
locker = DistributedLocker()
