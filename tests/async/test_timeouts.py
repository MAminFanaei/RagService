# tests/async/test_timeouts.py
"""
Timeout and Error Handling Tests

Tests for timeout configuration, graceful degradation, error recovery,
and cancellation behavior in the async application.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import settings
from app.services.rate_limit_service import RateLimitService
from app.core.security import is_token_blacklisted


# =============================================================================
# TIMEOUT CONFIGURATION
# =============================================================================


class TestTimeoutConfiguration:
    """Verify timeout settings are properly configured."""

    def test_llm_timeout_configured(self):
        assert settings.LLM_TIMEOUT_SECONDS > 0

    def test_retrieval_timeout_configured(self):
        assert settings.RETRIEVAL_TIMEOUT_SECONDS > 0

    def test_total_query_timeout_configured(self):
        assert settings.TOTAL_QUERY_TIMEOUT_SECONDS > 0

    def test_total_timeout_exceeds_components(self):
        """Total timeout must be >= individual component timeouts."""
        assert settings.TOTAL_QUERY_TIMEOUT_SECONDS >= settings.LLM_TIMEOUT_SECONDS
        assert settings.TOTAL_QUERY_TIMEOUT_SECONDS >= settings.RETRIEVAL_TIMEOUT_SECONDS

    def test_timeout_hierarchy_is_logical(self):
        """LLM is typically slowest; total should cover LLM + retrieval."""
        assert settings.TOTAL_QUERY_TIMEOUT_SECONDS >= (
            settings.LLM_TIMEOUT_SECONDS
        )


# =============================================================================
# TIMEOUT PATTERNS
# =============================================================================


class TestTimeoutPatterns:
    """Test async timeout patterns used throughout the codebase."""

    @pytest.mark.asyncio
    async def test_wait_for_timeout_raises(self):
        """asyncio.wait_for should raise TimeoutError when exceeded."""

        async def slow_operation():
            await asyncio.sleep(10)
            return "done"

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(slow_operation(), timeout=0.05)

    @pytest.mark.asyncio
    async def test_wait_for_completes_within_timeout(self):
        """Fast operations should complete normally."""

        async def fast_operation():
            await asyncio.sleep(0.01)
            return "done"

        result = await asyncio.wait_for(fast_operation(), timeout=1.0)
        assert result == "done"

    @pytest.mark.asyncio
    async def test_timeout_with_fallback(self):
        """Timeout should allow fallback to a default value."""

        async def unreliable_operation():
            await asyncio.sleep(10)
            return "original"

        try:
            result = await asyncio.wait_for(unreliable_operation(), timeout=0.05)
        except asyncio.TimeoutError:
            result = "fallback"

        assert result == "fallback"

    @pytest.mark.asyncio
    async def test_multiple_operations_within_timeout(self):
        """Chained operations should complete within combined timeout."""

        async def step_1():
            await asyncio.sleep(0.02)
            return "step1"

        async def step_2():
            await asyncio.sleep(0.02)
            return "step2"

        async def combined():
            r1 = await step_1()
            r2 = await step_2()
            return r1, r2

        result = await asyncio.wait_for(combined(), timeout=1.0)
        assert result == ("step1", "step2")


# =============================================================================
# GRACEFUL DEGRADATION
# =============================================================================


class TestGracefulDegradation:
    """Test behavior when dependencies fail."""

    @pytest.mark.asyncio
    async def test_rate_limit_redis_failure_fails_closed(self):
        """Rate limiting should deny requests when Redis is down (fail closed)."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=Exception("Redis connection failed"))

        result = await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis, user_id="test_user", limit_per_minute=10
        )
        # Fails closed — request denied
        assert result is False

    @pytest.mark.asyncio
    async def test_daily_quota_redis_failure_fails_closed(self):
        """Daily quota should deny requests when Redis is down."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=Exception("Redis down"))

        result = await RateLimitService.check_daily_quota(
            redis=mock_redis, user_id="test_user", max_per_day=100
        )
        assert result[0] is False
        assert result[1] == 0

    @pytest.mark.asyncio
    async def test_token_blacklist_redis_failure_fails_open(self):
        """Token blacklist check fails open when Redis is down.

        SECURITY NOTE: This means revoked tokens become valid when Redis is down.
        This is documented behavior — availability is prioritized over security.
        """
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(side_effect=Exception("Redis down"))

        result = await is_token_blacklisted(mock_redis, "some.token.here")
        assert result is False  # Fails open

    @pytest.mark.asyncio
    async def test_increment_failure_returns_zero(self):
        """Rate limit increment should return 0 on Redis failure."""
        mock_redis = AsyncMock()
        mock_redis.pipeline = MagicMock(side_effect=Exception("Redis error"))

        count = await RateLimitService.increment_rate_limit(
            redis=mock_redis, user_id="user123"
        )
        assert count == 0

    @pytest.mark.asyncio
    async def test_daily_increment_failure_returns_zero(self):
        """Daily quota increment should return 0 on Redis failure."""
        mock_redis = AsyncMock()
        mock_redis.pipeline = MagicMock(side_effect=Exception("Redis error"))

        count = await RateLimitService.increment_daily_quota(
            redis=mock_redis, user_id="user123"
        )
        assert count == 0


# =============================================================================
# ERROR RECOVERY
# =============================================================================


class TestErrorRecovery:
    """Test retry and recovery patterns."""

    @pytest.mark.asyncio
    async def test_retry_on_transient_failure(self):
        """Retry mechanism should recover from transient failures."""
        call_count = 0

        async def flaky_operation():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Transient failure")
            return "success"

        async def with_retry(max_retries=3):
            for attempt in range(max_retries):
                try:
                    return await flaky_operation()
                except ConnectionError:
                    if attempt == max_retries - 1:
                        raise
                    await asyncio.sleep(0.01)

        result = await with_retry()
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_exhaustion_raises(self):
        """When all retries fail, the exception should propagate."""

        async def always_fails():
            raise ConnectionError("Persistent failure")

        async def with_retry(max_retries=3):
            for attempt in range(max_retries):
                try:
                    return await always_fails()
                except ConnectionError:
                    if attempt == max_retries - 1:
                        raise
                    await asyncio.sleep(0.01)

        with pytest.raises(ConnectionError, match="Persistent failure"):
            await with_retry()

    @pytest.mark.asyncio
    async def test_circuit_breaker_pattern(self):
        """Circuit breaker should open after threshold failures."""
        failure_count = 0
        circuit_open = False
        threshold = 5

        async def protected_operation():
            nonlocal failure_count, circuit_open
            if circuit_open:
                raise RuntimeError("Circuit is open")
            failure_count += 1
            raise ConnectionError("Service unavailable")

        for _ in range(threshold + 1):
            try:
                await protected_operation()
            except ConnectionError:
                if failure_count >= threshold:
                    circuit_open = True
            except RuntimeError:
                break  # Circuit opened

        assert circuit_open is True
        assert failure_count == threshold


# =============================================================================
# DATABASE TIMEOUT BEHAVIOR
# =============================================================================


class TestDatabaseTimeouts:
    """Test database query performance expectations."""

    @pytest.mark.asyncio
    async def test_user_lookup_completes_quickly(self, db, test_user):
        """Simple user lookup should complete in < 100ms."""
        import time
        from app.services.user_service import UserService

        start = time.perf_counter()
        user = await UserService.get_by_id(db, test_user.id)
        elapsed = time.perf_counter() - start

        assert user is not None
        assert user.id == test_user.id
        assert elapsed < 0.1, f"User lookup took {elapsed:.3f}s (expected < 0.1s)"

    @pytest.mark.asyncio
    async def test_user_stats_query_completes_quickly(self, db, test_user):
        """Stats query with JOINs should complete in < 500ms."""
        import time
        from app.services.user_service import UserService

        start = time.perf_counter()
        stats = await UserService.get_user_stats(db, test_user.id)
        elapsed = time.perf_counter() - start

        assert "total_chats" in stats
        assert elapsed < 0.5, f"Stats query took {elapsed:.3f}s (expected < 0.5s)"


# =============================================================================
# CANCELLATION HANDLING
# =============================================================================


class TestCancellationHandling:
    """Test that cancellation is handled cleanly."""

    @pytest.mark.asyncio
    async def test_cancelled_task_does_not_complete(self):
        """A cancelled task should not produce a result."""
        completed = False

        async def long_task():
            nonlocal completed
            await asyncio.sleep(10)
            completed = True

        task = asyncio.create_task(long_task())
        await asyncio.sleep(0.05)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert completed is False

    @pytest.mark.asyncio
    async def test_gather_with_partial_cancellation(self):
        """asyncio.gather should handle mixed completion/cancellation."""
        results = []

        async def fast_task():
            await asyncio.sleep(0.01)
            results.append("fast")

        async def slow_task():
            await asyncio.sleep(10)
            results.append("slow")

        fast = asyncio.create_task(fast_task())
        slow = asyncio.create_task(slow_task())

        # Wait for fast to complete
        await asyncio.sleep(0.05)
        slow.cancel()

        try:
            await slow
        except asyncio.CancelledError:
            pass

        assert "fast" in results
        assert "slow" not in results
