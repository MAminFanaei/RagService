# tests/unit/test_rate_limiter.py
"""
Unit tests for RateLimitService.

All methods are async. Tests use mock_redis from conftest.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from app.services.rate_limit_service import RateLimitService


# =============================================================================
# HELPERS
# =============================================================================


class TestHelperFunctions:
    def test_seconds_until_midnight_positive(self):
        seconds = RateLimitService._seconds_until_midnight_utc()
        assert 0 < seconds <= 86400

    def test_seconds_until_midnight_is_int(self):
        assert isinstance(RateLimitService._seconds_until_midnight_utc(), int)

    def test_get_user_limits_defaults(self):
        mock_user = MagicMock()
        mock_user.rate_limit_per_minute = None
        mock_user.max_messages_per_day = None
        rpm, qpd = RateLimitService.get_user_limits(mock_user)
        assert rpm > 0
        assert qpd > 0

    def test_get_user_limits_custom(self):
        mock_user = MagicMock()
        mock_user.rate_limit_per_minute = 50
        mock_user.max_messages_per_day = 500
        rpm, qpd = RateLimitService.get_user_limits(mock_user)
        assert rpm >= 50
        assert qpd >= 500

    def test_get_user_limits_zero_uses_default(self):
        mock_user = MagicMock()
        mock_user.rate_limit_per_minute = 0
        mock_user.max_messages_per_day = 0
        rpm, qpd = RateLimitService.get_user_limits(mock_user)
        assert rpm > 0
        assert qpd > 0


# =============================================================================
# PER-MINUTE RATE LIMIT
# =============================================================================


class TestPerMinuteRateLimit:
    @pytest.mark.asyncio
    async def test_allowed_under_limit(self, mock_redis):
        mock_redis.get = AsyncMock(return_value="5")
        allowed = await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis, user_id="user123", limit_per_minute=10
        )
        assert allowed is True

    @pytest.mark.asyncio
    async def test_denied_at_limit(self, mock_redis):
        mock_redis.get = AsyncMock(return_value="10")
        allowed = await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis, user_id="user123", limit_per_minute=10
        )
        assert allowed is False

    @pytest.mark.asyncio
    async def test_denied_over_limit(self, mock_redis):
        mock_redis.get = AsyncMock(return_value="15")
        allowed = await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis, user_id="user123", limit_per_minute=10
        )
        assert allowed is False

    @pytest.mark.asyncio
    async def test_allowed_no_previous(self, mock_redis):
        mock_redis.get = AsyncMock(return_value=None)
        allowed = await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis, user_id="user123", limit_per_minute=10
        )
        assert allowed is True

    @pytest.mark.asyncio
    async def test_redis_error_fails_closed(self, mock_redis):
        """Rate limit check returns False on Redis error (fail closed)."""
        mock_redis.get = AsyncMock(side_effect=Exception("Redis error"))
        allowed = await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis, user_id="user123", limit_per_minute=10
        )
        assert allowed is False

    @pytest.mark.asyncio
    async def test_increment_rate_limit(self, mock_redis):
        pipeline_mock = AsyncMock()
        pipeline_mock.incr = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.expire = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.execute = AsyncMock(return_value=[6, True])
        pipeline_mock.__aenter__ = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.__aexit__ = AsyncMock(return_value=None)
        mock_redis.pipeline = MagicMock(return_value=pipeline_mock)

        count = await RateLimitService.increment_rate_limit(redis=mock_redis, user_id="user123")
        assert count == 6

    @pytest.mark.asyncio
    async def test_increment_redis_error_returns_zero(self, mock_redis):
        mock_redis.pipeline = MagicMock(side_effect=Exception("Redis error"))
        count = await RateLimitService.increment_rate_limit(redis=mock_redis, user_id="user123")
        assert count == 0


# =============================================================================
# DAILY QUOTA
# =============================================================================


class TestDailyQuota:
    @pytest.mark.asyncio
    async def test_allowed_under_quota(self, mock_redis):
        mock_redis.get = AsyncMock(return_value="50")
        allowed, remaining = await RateLimitService.check_daily_quota(
            redis=mock_redis, user_id="user123", max_per_day=100
        )
        assert allowed is True
        assert remaining == 50

    @pytest.mark.asyncio
    async def test_denied_at_quota(self, mock_redis):
        mock_redis.get = AsyncMock(return_value="100")
        allowed, remaining = await RateLimitService.check_daily_quota(
            redis=mock_redis, user_id="user123", max_per_day=100
        )
        assert allowed is False
        assert remaining == 0

    @pytest.mark.asyncio
    async def test_denied_over_quota(self, mock_redis):
        mock_redis.get = AsyncMock(return_value="150")
        allowed, remaining = await RateLimitService.check_daily_quota(
            redis=mock_redis, user_id="user123", max_per_day=100
        )
        assert allowed is False
        assert remaining == 0

    @pytest.mark.asyncio
    async def test_allowed_no_previous(self, mock_redis):
        mock_redis.get = AsyncMock(return_value=None)
        allowed, remaining = await RateLimitService.check_daily_quota(
            redis=mock_redis, user_id="user123", max_per_day=100
        )
        assert allowed is True
        assert remaining == 100

    @pytest.mark.asyncio
    async def test_redis_error_fails_closed(self, mock_redis):
        """Daily quota check fails closed on Redis error."""
        mock_redis.get = AsyncMock(side_effect=Exception("Redis error"))
        result = await RateLimitService.check_daily_quota(
            redis=mock_redis, user_id="user123", max_per_day=100
        )
        # Returns (False, 0) on error
        assert result[0] is False
        assert result[1] == 0

    @pytest.mark.asyncio
    async def test_increment_daily_quota(self, mock_redis):
        pipeline_mock = AsyncMock()
        pipeline_mock.incr = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.expire = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.execute = AsyncMock(return_value=[51, True])
        pipeline_mock.__aenter__ = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.__aexit__ = AsyncMock(return_value=None)
        mock_redis.pipeline = MagicMock(return_value=pipeline_mock)

        count = await RateLimitService.increment_daily_quota(redis=mock_redis, user_id="user123")
        assert count == 51

    @pytest.mark.asyncio
    async def test_increment_daily_redis_error_returns_zero(self, mock_redis):
        mock_redis.pipeline = MagicMock(side_effect=Exception("Redis error"))
        count = await RateLimitService.increment_daily_quota(redis=mock_redis, user_id="user123")
        assert count == 0


# =============================================================================
# KEY GENERATION
# =============================================================================


class TestKeyGeneration:
    @pytest.mark.asyncio
    async def test_key_contains_user_id(self, mock_redis):
        mock_redis.get = AsyncMock(return_value=None)
        await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis, user_id="user_abc123", limit_per_minute=10
        )
        key = mock_redis.get.call_args[0][0]
        assert "user_abc123" in key

    @pytest.mark.asyncio
    async def test_key_contains_date(self, mock_redis):
        mock_redis.get = AsyncMock(return_value=None)
        await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis, user_id="user123", limit_per_minute=10
        )
        key = mock_redis.get.call_args[0][0]
        assert datetime.now(timezone.utc).strftime("%Y%m%d") in key


# =============================================================================
# FLOW TESTS
# =============================================================================


class TestRateLimitFlow:
    @pytest.mark.asyncio
    async def test_check_then_increment(self, mock_redis):
        mock_redis.get = AsyncMock(return_value="5")
        allowed = await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis, user_id="user123", limit_per_minute=10
        )
        assert allowed is True

        pipeline_mock = AsyncMock()
        pipeline_mock.incr = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.expire = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.execute = AsyncMock(return_value=[6, True])
        pipeline_mock.__aenter__ = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.__aexit__ = AsyncMock(return_value=None)
        mock_redis.pipeline = MagicMock(return_value=pipeline_mock)

        count = await RateLimitService.increment_rate_limit(redis=mock_redis, user_id="user123")
        assert count == 6

    @pytest.mark.asyncio
    async def test_both_checks_pass(self, mock_redis):
        mock_redis.get = AsyncMock(return_value="5")
        rate_ok = await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis, user_id="user123", limit_per_minute=10
        )
        quota_ok, remaining = await RateLimitService.check_daily_quota(
            redis=mock_redis, user_id="user123", max_per_day=100
        )
        assert rate_ok is True
        assert quota_ok is True
        assert remaining == 95


# =============================================================================
# EDGE CASES
# =============================================================================


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_zero_limit_denies(self, mock_redis):
        mock_redis.get = AsyncMock(return_value="0")
        allowed = await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis, user_id="user123", limit_per_minute=0
        )
        assert allowed is False

    @pytest.mark.asyncio
    async def test_special_characters_in_user_id(self, mock_redis):
        mock_redis.get = AsyncMock(return_value=None)
        # Should not raise
        await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis, user_id="user:123:test", limit_per_minute=10
        )
        mock_redis.get.assert_called_once()
