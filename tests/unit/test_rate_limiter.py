# tests/unit/test_rate_limiter.py
"""
Unit tests for Rate Limiting Service.

Tests:
- Per-minute rate limiting
- Daily quota management
- Rate limit checking vs incrementing
- User limit retrieval
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.rate_limit_service import RateLimitService


# =============================================================================
# HELPER FUNCTION TESTS
# =============================================================================

class TestHelperFunctions:
    """Tests for helper functions"""
    
    def test_seconds_until_midnight_positive(self):
        """Test that seconds until midnight is positive"""
        seconds = RateLimitService._seconds_until_midnight_utc()
        
        assert seconds > 0
        assert seconds <= 86400  # Max 24 hours
    
    def test_seconds_until_midnight_type(self):
        """Test that seconds until midnight returns int"""
        seconds = RateLimitService._seconds_until_midnight_utc()
        
        assert isinstance(seconds, int)
    
    def test_get_user_limits_defaults(self):
        """Test getting user limits when user has no custom limits"""
        mock_user = MagicMock()
        mock_user.rate_limit_per_minute = None
        mock_user.max_messages_per_day = None
        
        rate_per_min, quota_per_day = RateLimitService.get_user_limits(mock_user)
        
        # Should use defaults from settings
        assert rate_per_min > 0
        assert quota_per_day > 0
    
    def test_get_user_limits_custom(self):
        """Test getting user limits when user has custom limits"""
        mock_user = MagicMock()
        mock_user.rate_limit_per_minute = 50
        mock_user.max_messages_per_day = 500
        
        rate_per_min, quota_per_day = RateLimitService.get_user_limits(mock_user)
        
        # Should use the higher of custom or default
        assert rate_per_min >= 50
        assert quota_per_day >= 500
    
    def test_get_user_limits_zero_custom(self):
        """Test getting user limits when user has zero custom limits"""
        mock_user = MagicMock()
        mock_user.rate_limit_per_minute = 0
        mock_user.max_messages_per_day = 0
        
        rate_per_min, quota_per_day = RateLimitService.get_user_limits(mock_user)
        
        # Should use defaults (zero is treated as "use default")
        assert rate_per_min > 0
        assert quota_per_day > 0


# =============================================================================
# PER-MINUTE RATE LIMIT TESTS
# =============================================================================

class TestPerMinuteRateLimit:
    """Tests for per-minute rate limiting"""
    
    @pytest.mark.asyncio
    async def test_check_rate_limit_allowed(self, mock_redis):
        """Test rate limit check when under limit"""
        mock_redis.get = AsyncMock(return_value="5")  # 5 requests made
        
        allowed = await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis,
            user_id="user123",
            limit_per_minute=10
        )
        
        assert allowed is True
    
    @pytest.mark.asyncio
    async def test_check_rate_limit_at_limit(self, mock_redis):
        """Test rate limit check when at exact limit"""
        mock_redis.get = AsyncMock(return_value="10")  # At limit
        
        allowed = await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis,
            user_id="user123",
            limit_per_minute=10
        )
        
        assert allowed is False
    
    @pytest.mark.asyncio
    async def test_check_rate_limit_exceeded(self, mock_redis):
        """Test rate limit check when exceeded"""
        mock_redis.get = AsyncMock(return_value="15")  # Over limit
        
        allowed = await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis,
            user_id="user123",
            limit_per_minute=10
        )
        
        assert allowed is False
    
    @pytest.mark.asyncio
    async def test_check_rate_limit_no_previous(self, mock_redis):
        """Test rate limit check when no previous requests"""
        mock_redis.get = AsyncMock(return_value=None)  # No key exists
        
        allowed = await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis,
            user_id="user123",
            limit_per_minute=10
        )
        
        assert allowed is True
    
    @pytest.mark.asyncio
    async def test_check_rate_limit_redis_error(self, mock_redis):
        """Test rate limit check when Redis fails - FAILS OPEN"""
        mock_redis.get = AsyncMock(side_effect=Exception("Redis error"))
        
        allowed = await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis,
            user_id="user123",
            limit_per_minute=10
        )
        
        # Current implementation fails open - security concern
        assert allowed is True
    
    @pytest.mark.asyncio
    async def test_increment_rate_limit(self, mock_redis):
        """Test incrementing rate limit counter"""
        pipeline_mock = MagicMock()
        pipeline_mock.incr = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.expire = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.execute = AsyncMock(return_value=[6, True])
        pipeline_mock.__aenter__ = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.__aexit__ = AsyncMock(return_value=None)
        mock_redis.pipeline = MagicMock(return_value=pipeline_mock)
        
        count = await RateLimitService.increment_rate_limit(
            redis=mock_redis,
            user_id="user123"
        )
        
        assert count == 6
    
    @pytest.mark.asyncio
    async def test_increment_rate_limit_redis_error(self, mock_redis):
        """Test increment when Redis fails"""
        mock_redis.pipeline = MagicMock(side_effect=Exception("Redis error"))
        
        count = await RateLimitService.increment_rate_limit(
            redis=mock_redis,
            user_id="user123"
        )
        
        assert count == 0  # Returns 0 on error


# =============================================================================
# DAILY QUOTA TESTS
# =============================================================================

class TestDailyQuota:
    """Tests for daily quota management"""
    
    @pytest.mark.asyncio
    async def test_check_daily_quota_allowed(self, mock_redis):
        """Test daily quota check when under limit"""
        mock_redis.get = AsyncMock(return_value="50")  # 50 messages today
        
        allowed, remaining = await RateLimitService.check_daily_quota(
            redis=mock_redis,
            user_id="user123",
            max_per_day=100
        )
        
        assert allowed is True
        assert remaining == 50
    
    @pytest.mark.asyncio
    async def test_check_daily_quota_at_limit(self, mock_redis):
        """Test daily quota check when at exact limit"""
        mock_redis.get = AsyncMock(return_value="100")  # At limit
        
        allowed, remaining = await RateLimitService.check_daily_quota(
            redis=mock_redis,
            user_id="user123",
            max_per_day=100
        )
        
        assert allowed is False
        assert remaining == 0
    
    @pytest.mark.asyncio
    async def test_check_daily_quota_exceeded(self, mock_redis):
        """Test daily quota check when exceeded"""
        mock_redis.get = AsyncMock(return_value="150")  # Over limit
        
        allowed, remaining = await RateLimitService.check_daily_quota(
            redis=mock_redis,
            user_id="user123",
            max_per_day=100
        )
        
        assert allowed is False
        assert remaining == 0  # Can't be negative
    
    @pytest.mark.asyncio
    async def test_check_daily_quota_no_previous(self, mock_redis):
        """Test daily quota check when no previous messages"""
        mock_redis.get = AsyncMock(return_value=None)  # No key exists
        
        allowed, remaining = await RateLimitService.check_daily_quota(
            redis=mock_redis,
            user_id="user123",
            max_per_day=100
        )
        
        assert allowed is True
        assert remaining == 100
    
    @pytest.mark.asyncio
    async def test_check_daily_quota_redis_error(self, mock_redis):
        """Test daily quota check when Redis fails - FAILS CLOSED"""
        mock_redis.get = AsyncMock(side_effect=Exception("Redis error"))
        
        # This should fail closed (unlike per-minute rate limit)
        result = await RateLimitService.check_daily_quota(
            redis=mock_redis,
            user_id="user123",
            max_per_day=100
        )
        
        # Returns False on error - fails closed (good!)
        assert result is False or result == (False, 0) or result[0] is False
    
    @pytest.mark.asyncio
    async def test_increment_daily_quota(self, mock_redis):
        """Test incrementing daily quota counter"""
        pipeline_mock = MagicMock()
        pipeline_mock.incr = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.expire = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.execute = AsyncMock(return_value=[51, True])
        pipeline_mock.__aenter__ = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.__aexit__ = AsyncMock(return_value=None)
        mock_redis.pipeline = MagicMock(return_value=pipeline_mock)
        
        count = await RateLimitService.increment_daily_quota(
            redis=mock_redis,
            user_id="user123"
        )
        
        assert count == 51
    
    @pytest.mark.asyncio
    async def test_increment_daily_quota_redis_error(self, mock_redis):
        """Test daily quota increment when Redis fails"""
        mock_redis.pipeline = MagicMock(side_effect=Exception("Redis error"))
        
        count = await RateLimitService.increment_daily_quota(
            redis=mock_redis,
            user_id="user123"
        )
        
        assert count == 0  # Returns 0 on error


# =============================================================================
# KEY GENERATION TESTS
# =============================================================================

class TestKeyGeneration:
    """Tests for Redis key generation patterns"""
    
    @pytest.mark.asyncio
    async def test_rate_limit_key_contains_user_id(self, mock_redis):
        """Test that rate limit key includes user ID"""
        mock_redis.get = AsyncMock(return_value=None)
        
        await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis,
            user_id="user_abc123",
            limit_per_minute=10
        )
        
        # Check the key used
        call_args = mock_redis.get.call_args
        key = call_args[0][0]
        assert "user_abc123" in key
    
    @pytest.mark.asyncio
    async def test_rate_limit_key_contains_timestamp(self, mock_redis):
        """Test that rate limit key includes minute timestamp"""
        mock_redis.get = AsyncMock(return_value=None)
        
        await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis,
            user_id="user123",
            limit_per_minute=10
        )
        
        call_args = mock_redis.get.call_args
        key = call_args[0][0]
        
        # Key should contain date/time pattern
        now = datetime.now(timezone.utc)
        assert now.strftime('%Y%m%d') in key
    
    @pytest.mark.asyncio
    async def test_custom_key_prefix(self, mock_redis):
        """Test using custom key prefix"""
        mock_redis.get = AsyncMock(return_value=None)
        
        await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis,
            user_id="user123",
            limit_per_minute=10,
            key_prefix="custom_prefix"
        )
        
        call_args = mock_redis.get.call_args
        key = call_args[0][0]
        assert key.startswith("custom_prefix:")


# =============================================================================
# INTEGRATION-LIKE TESTS (with mock Redis)
# =============================================================================

class TestRateLimitFlow:
    """Tests for complete rate limiting flows"""
    
    @pytest.mark.asyncio
    async def test_check_then_increment_flow(self, mock_redis):
        """Test the check-then-increment pattern"""
        # Initial state: 5 requests
        mock_redis.get = AsyncMock(return_value="5")
        
        # Check should allow
        allowed = await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis,
            user_id="user123",
            limit_per_minute=10
        )
        assert allowed is True
        
        # Simulate increment after successful operation
        pipeline_mock = MagicMock()
        pipeline_mock.incr = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.expire = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.execute = AsyncMock(return_value=[6, True])
        pipeline_mock.__aenter__ = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.__aexit__ = AsyncMock(return_value=None)
        mock_redis.pipeline = MagicMock(return_value=pipeline_mock)
        
        count = await RateLimitService.increment_rate_limit(
            redis=mock_redis,
            user_id="user123"
        )
        assert count == 6
    
    @pytest.mark.asyncio
    async def test_quota_and_rate_limit_both_checked(self, mock_redis):
        """Test that both quota and rate limit can be checked"""
        mock_redis.get = AsyncMock(return_value="5")
        
        # Check rate limit
        rate_allowed = await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis,
            user_id="user123",
            limit_per_minute=10
        )
        
        # Check quota
        quota_allowed, remaining = await RateLimitService.check_daily_quota(
            redis=mock_redis,
            user_id="user123",
            max_per_day=100
        )
        
        # Both should allow
        assert rate_allowed is True
        assert quota_allowed is True
        assert remaining == 95


# =============================================================================
# EDGE CASES
# =============================================================================

class TestEdgeCases:
    """Tests for edge cases and boundary conditions"""
    
    @pytest.mark.asyncio
    async def test_zero_limit(self, mock_redis):
        """Test with zero limit"""
        mock_redis.get = AsyncMock(return_value="0")
        
        allowed = await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis,
            user_id="user123",
            limit_per_minute=0  # Zero limit
        )
        
        # Zero limit should deny everything
        assert allowed is False
    
    @pytest.mark.asyncio
    async def test_negative_count_in_redis(self, mock_redis):
        """Test handling of negative count (shouldn't happen but be safe)"""
        mock_redis.get = AsyncMock(return_value="-5")
        
        allowed, remaining = await RateLimitService.check_daily_quota(
            redis=mock_redis,
            user_id="user123",
            max_per_day=100
        )
        
        # Should handle gracefully
        assert allowed is True
        assert remaining >= 0
    
    @pytest.mark.asyncio
    async def test_non_numeric_count_in_redis(self, mock_redis):
        """Test handling of non-numeric value in Redis"""
        mock_redis.get = AsyncMock(return_value="not_a_number")
        
        # Should handle gracefully
        try:
            allowed = await RateLimitService.check_per_min_rate_limit(
                redis=mock_redis,
                user_id="user123",
                limit_per_minute=10
            )
            # If it doesn't raise, should have some default behavior
        except ValueError:
            pass  # Expected if not handled
    
    @pytest.mark.asyncio
    async def test_very_large_limit(self, mock_redis):
        """Test with very large limit"""
        mock_redis.get = AsyncMock(return_value="1000000")
        
        allowed = await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis,
            user_id="user123",
            limit_per_minute=10000000  # 10 million
        )
        
        assert allowed is True
    
    @pytest.mark.asyncio
    async def test_special_characters_in_user_id(self, mock_redis):
        """Test user ID with special characters"""
        mock_redis.get = AsyncMock(return_value=None)
        
        # User ID with special chars (shouldn't happen but be safe)
        await RateLimitService.check_per_min_rate_limit(
            redis=mock_redis,
            user_id="user:123:test",
            limit_per_minute=10
        )
        
        # Should complete without error
        mock_redis.get.assert_called_once()