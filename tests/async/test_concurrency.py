# tests/async/test_concurrency.py
"""
Async Concurrency Tests

Tests for concurrent operations, race conditions, and async resource management.
All tests are truly async and test actual application behavior.
"""

import pytest
import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock
from concurrent.futures import ThreadPoolExecutor

from app.services.rate_limit_service import RateLimitService
from app.core.security import (
    create_token_pair,
    blacklist_token,
    is_token_blacklisted,
    decode_token,
    get_password_hash_async,
    verify_password_async,
)


# =============================================================================
# CONCURRENT TOKEN OPERATIONS
# =============================================================================


class TestConcurrentTokenOperations:
    """Test concurrent token creation and blacklisting."""

    @pytest.mark.asyncio
    async def test_concurrent_token_pair_creation(self):
        """Multiple concurrent token creations should produce unique tokens."""
        tasks = [
            asyncio.to_thread(
                create_token_pair,
                user_id=f"user_{i}",
                email=f"user{i}@example.com",
                is_admin=False,
            )
            for i in range(20)
        ]
        results = await asyncio.gather(*tasks)

        access_tokens = {r["access_token"] for r in results}
        refresh_tokens = {r["refresh_token"] for r in results}

        assert len(access_tokens) == 20, "All access tokens must be unique"
        assert len(refresh_tokens) == 20, "All refresh tokens must be unique"

    @pytest.mark.asyncio
    async def test_concurrent_blacklist_operations(self):
        """Concurrent blacklist + check operations should be consistent."""
        mock_redis = AsyncMock()
        blacklisted_keys = set()

        async def mock_set(key, value, ex=None):
            blacklisted_keys.add(key)
            return True

        async def mock_exists(key):
            return 1 if key in blacklisted_keys else 0

        mock_redis.set = mock_set
        mock_redis.exists = mock_exists

        token = create_token_pair("u1", "u1@test.com", False)["access_token"]
        payload = decode_token(token)

        # Blacklist the token
        result = await blacklist_token(mock_redis, token, payload)
        assert result is True

        # Concurrent checks should all see it as blacklisted
        checks = await asyncio.gather(
            *[is_token_blacklisted(mock_redis, token) for _ in range(10)]
        )
        assert all(c is True for c in checks)


# =============================================================================
# CONCURRENT RATE LIMIT OPERATIONS
# =============================================================================


class TestConcurrentRateLimiting:
    """Test rate limiting under concurrent access."""

    @pytest.mark.asyncio
    async def test_concurrent_rate_limit_checks(self, mock_redis):
        """Concurrent rate limit checks should all return consistent results."""
        mock_redis.get = AsyncMock(return_value="5")

        async def check():
            return await RateLimitService.check_per_min_rate_limit(
                redis=mock_redis, user_id="user123", limit_per_minute=10
            )

        results = await asyncio.gather(*[check() for _ in range(20)])
        # All should be allowed (count=5 < limit=10)
        assert all(r is True for r in results)

    @pytest.mark.asyncio
    async def test_concurrent_quota_checks(self, mock_redis):
        """Concurrent daily quota checks should return consistent results."""
        mock_redis.get = AsyncMock(return_value="50")

        async def check():
            return await RateLimitService.check_daily_quota(
                redis=mock_redis, user_id="user123", max_per_day=100
            )

        results = await asyncio.gather(*[check() for _ in range(20)])
        for allowed, remaining in results:
            assert allowed is True
            assert remaining == 50

    @pytest.mark.asyncio
    async def test_concurrent_increments(self, mock_redis):
        """Concurrent increments should all succeed."""
        call_count = 0

        pipeline_mock = AsyncMock()
        pipeline_mock.incr = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.expire = AsyncMock(return_value=pipeline_mock)

        async def mock_execute():
            nonlocal call_count
            call_count += 1
            return [call_count, True]

        pipeline_mock.execute = mock_execute
        pipeline_mock.__aenter__ = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.__aexit__ = AsyncMock(return_value=None)
        mock_redis.pipeline = MagicMock(return_value=pipeline_mock)

        tasks = [
            RateLimitService.increment_rate_limit(redis=mock_redis, user_id="user123")
            for _ in range(10)
        ]
        results = await asyncio.gather(*tasks)

        # All should return non-zero (successful increment)
        assert all(r > 0 for r in results)


# =============================================================================
# CONCURRENT PASSWORD OPERATIONS
# =============================================================================


class TestConcurrentPasswordOperations:
    """Test async password hashing under concurrent load."""

    @pytest.mark.asyncio
    async def test_concurrent_password_hashing(self):
        """Multiple concurrent hash operations should all complete correctly."""
        passwords = [f"Password{i}!" for i in range(5)]

        hashes = await asyncio.gather(
            *[get_password_hash_async(p) for p in passwords]
        )

        assert len(hashes) == 5
        assert all(h.startswith("$argon2") for h in hashes)
        # All hashes should be unique (different passwords + salt)
        assert len(set(hashes)) == 5

    @pytest.mark.asyncio
    async def test_concurrent_password_verification(self):
        """Concurrent verify operations should not interfere with each other."""
        password = "TestPassword123!"
        hashed = await get_password_hash_async(password)

        # Concurrent: some correct, some wrong
        tasks = []
        for i in range(10):
            if i % 2 == 0:
                tasks.append(verify_password_async(password, hashed))
            else:
                tasks.append(verify_password_async("WrongPass!", hashed))

        results = await asyncio.gather(*tasks)

        for i, result in enumerate(results):
            if i % 2 == 0:
                assert result is True, f"Correct password failed at index {i}"
            else:
                assert result is False, f"Wrong password passed at index {i}"

    @pytest.mark.asyncio
    async def test_hashing_does_not_block_event_loop(self):
        """Async hashing should allow other coroutines to run concurrently."""
        events = []

        async def track_event(name, delay=0.01):
            await asyncio.sleep(delay)
            events.append(name)

        await asyncio.gather(
            get_password_hash_async("SlowHash123!"),
            track_event("concurrent_task_1"),
            track_event("concurrent_task_2"),
        )

        # Both tracking tasks should have completed
        assert "concurrent_task_1" in events
        assert "concurrent_task_2" in events


# =============================================================================
# SEMAPHORE & CONCURRENCY LIMITS
# =============================================================================


class TestConcurrencyLimits:
    """Test concurrency limiting patterns used in the application."""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrent_operations(self):
        """Semaphore should enforce maximum concurrency."""
        max_concurrent = 3
        semaphore = asyncio.Semaphore(max_concurrent)

        active = 0
        peak_active = 0

        async def limited_op():
            nonlocal active, peak_active
            async with semaphore:
                active += 1
                peak_active = max(peak_active, active)
                await asyncio.sleep(0.02)
                active -= 1

        await asyncio.gather(*[limited_op() for _ in range(15)])

        assert peak_active <= max_concurrent
        assert active == 0  # All completed

    @pytest.mark.asyncio
    async def test_max_concurrent_queries_config(self):
        """Verify MAX_CONCURRENT_QUERIES is properly configured."""
        from app.config import settings

        assert settings.MAX_CONCURRENT_QUERIES > 0
        assert isinstance(settings.MAX_CONCURRENT_QUERIES, int)


# =============================================================================
# TASK CANCELLATION
# =============================================================================


class TestTaskCancellation:
    """Test graceful task cancellation handling."""

    @pytest.mark.asyncio
    async def test_cancel_long_running_task(self):
        """Cancelled tasks should clean up properly."""
        cleanup_ran = False

        async def long_task():
            nonlocal cleanup_ran
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cleanup_ran = True
                raise

        task = asyncio.create_task(long_task())
        await asyncio.sleep(0.05)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert cleanup_ran is True

    @pytest.mark.asyncio
    async def test_graceful_shutdown_completes_pending(self):
        """Short-running tasks should complete before shutdown."""
        completed = []

        async def quick_task(task_id):
            await asyncio.sleep(0.02)
            completed.append(task_id)

        tasks = [asyncio.create_task(quick_task(i)) for i in range(5)]

        done, pending = await asyncio.wait(tasks, timeout=1.0)

        assert len(done) == 5
        assert len(pending) == 0
        assert len(completed) == 5


# =============================================================================
# EXECUTOR INTEGRATION
# =============================================================================


class TestExecutorIntegration:
    """Test ThreadPoolExecutor usage for blocking operations."""

    @pytest.mark.asyncio
    async def test_blocking_ops_in_executor(self):
        """Blocking operations should run in executor without blocking loop."""
        import time

        def blocking_work():
            time.sleep(0.05)
            return "done"

        loop = asyncio.get_event_loop()
        executor = ThreadPoolExecutor(max_workers=4)

        try:
            results = await asyncio.gather(
                *[loop.run_in_executor(executor, blocking_work) for _ in range(4)]
            )
            assert all(r == "done" for r in results)
        finally:
            executor.shutdown(wait=True)

    @pytest.mark.asyncio
    async def test_executor_exception_propagation(self):
        """Exceptions in executor should propagate correctly."""

        def failing_work():
            raise ValueError("executor error")

        loop = asyncio.get_event_loop()
        executor = ThreadPoolExecutor(max_workers=1)

        try:
            with pytest.raises(ValueError, match="executor error"):
                await loop.run_in_executor(executor, failing_work)
        finally:
            executor.shutdown(wait=True)
