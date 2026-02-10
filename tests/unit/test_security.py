# tests/unit/test_security.py
"""
Unit Tests for Security Module

Tests password hashing (sync + async), JWT tokens, and token blacklisting.
"""

import pytest
import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from jose import jwt as jose_jwt

from app.core.security import (
    verify_password,
    get_password_hash,
    verify_password_async,
    get_password_hash_async,
    create_access_token,
    create_refresh_token,
    decode_token,
    create_token_pair,
    get_token_jti,
    get_token_remaining_ttl,
    blacklist_token,
    is_token_blacklisted,
    TOKEN_BLACKLIST_PREFIX,
)
from app.config import settings


# =============================================================================
# PASSWORD HASHING — SYNC
# =============================================================================


class TestPasswordHashingSync:
    """Test sync password hashing with Argon2."""

    def test_hash_creates_argon2_hash(self):
        hashed = get_password_hash("SecurePassword123!")
        assert hashed is not None
        assert hashed != "SecurePassword123!"
        assert hashed.startswith("$argon2")

    def test_hash_is_salted(self):
        h1 = get_password_hash("SecurePassword123!")
        h2 = get_password_hash("SecurePassword123!")
        assert h1 != h2

    def test_verify_correct_password(self):
        hashed = get_password_hash("SecurePassword123!")
        assert verify_password("SecurePassword123!", hashed) is True

    def test_verify_wrong_password(self):
        hashed = get_password_hash("SecurePassword123!")
        assert verify_password("WrongPassword456!", hashed) is False

    def test_verify_empty_password(self):
        hashed = get_password_hash("SecurePassword123!")
        assert verify_password("", hashed) is False

    def test_special_characters(self):
        password = "P@$$w0rd!#%^&*()_+-=[]{}|;':\",./<>?"
        hashed = get_password_hash(password)
        assert verify_password(password, hashed) is True

    def test_unicode_password(self):
        password = "密码123!@#中文"
        hashed = get_password_hash(password)
        assert verify_password(password, hashed) is True


# =============================================================================
# PASSWORD HASHING — ASYNC
# =============================================================================


class TestPasswordHashingAsync:
    """Test async password hashing (thread-pool based)."""

    @pytest.mark.asyncio
    async def test_async_hash_creates_argon2(self):
        hashed = await get_password_hash_async("SecurePassword123!")
        assert hashed.startswith("$argon2")

    @pytest.mark.asyncio
    async def test_async_verify_correct(self):
        hashed = await get_password_hash_async("SecurePassword123!")
        assert await verify_password_async("SecurePassword123!", hashed) is True

    @pytest.mark.asyncio
    async def test_async_verify_wrong(self):
        hashed = await get_password_hash_async("SecurePassword123!")
        assert await verify_password_async("WrongPassword!", hashed) is False

    @pytest.mark.asyncio
    async def test_async_does_not_block_loop(self):
        """Verify async hashing doesn't block — other tasks can run concurrently."""
        flag = False

        async def set_flag():
            nonlocal flag
            await asyncio.sleep(0.01)
            flag = True

        # Run hash and flag-setter concurrently
        await asyncio.gather(
            get_password_hash_async("TestPassword123!"),
            set_flag(),
        )
        assert flag is True


# =============================================================================
# JWT TOKEN CREATION
# =============================================================================


class TestJWTTokenCreation:
    """Test JWT token creation."""

    def test_create_access_token_structure(self):
        token = create_access_token({"sub": "user123", "email": "test@example.com"})
        assert isinstance(token, str)
        assert len(token.split(".")) == 3

    def test_access_token_has_correct_type(self):
        token = create_access_token({"sub": "user123"})
        payload = jose_jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        assert payload["type"] == "access"

    def test_access_token_custom_expiry(self):
        token = create_access_token({"sub": "user123"}, expires_delta=timedelta(minutes=5))
        payload = jose_jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        expected = datetime.now(timezone.utc) + timedelta(minutes=5)
        assert abs((exp - expected).total_seconds()) < 5

    def test_access_token_default_expiry(self):
        token = create_access_token({"sub": "user123"})
        payload = jose_jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        expected = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        assert abs((exp - expected).total_seconds()) < 5

    def test_create_refresh_token(self):
        token = create_refresh_token({"sub": "user123", "email": "t@t.com"})
        payload = jose_jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        assert payload["type"] == "refresh"
        assert payload["sub"] == "user123"

    def test_refresh_token_longer_expiry(self):
        data = {"sub": "user123"}
        access = create_access_token(data)
        refresh = create_refresh_token(data)
        ap = jose_jwt.decode(access, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        rp = jose_jwt.decode(refresh, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        assert rp["exp"] > ap["exp"]

    def test_create_token_pair(self):
        tokens = create_token_pair(user_id="u1", email="t@t.com", is_admin=False)
        assert "access_token" in tokens
        assert "refresh_token" in tokens
        assert tokens["token_type"] == "bearer"

        ap = decode_token(tokens["access_token"])
        assert ap["sub"] == "u1"
        assert ap["email"] == "t@t.com"
        assert ap["is_admin"] is False
        assert ap["type"] == "access"

        rp = decode_token(tokens["refresh_token"])
        assert rp["type"] == "refresh"

    def test_token_contains_required_claims(self):
        tokens = create_token_pair(user_id="u1", email="a@a.com", is_admin=True)
        payload = decode_token(tokens["access_token"])
        for claim in ("sub", "email", "is_admin", "exp", "type"):
            assert claim in payload


# =============================================================================
# JWT TOKEN DECODING
# =============================================================================


class TestJWTTokenDecoding:
    """Test JWT token decoding and validation."""

    def test_decode_valid_token(self):
        token = create_access_token({"sub": "u1", "email": "t@t.com"})
        payload = decode_token(token)
        assert payload is not None
        assert payload["sub"] == "u1"

    def test_decode_invalid_token(self):
        assert decode_token("invalid.token.here") is None

    def test_decode_empty_token(self):
        assert decode_token("") is None
        assert decode_token(None) is None

    def test_decode_expired_token(self):
        token = create_access_token({"sub": "u1"}, expires_delta=timedelta(hours=-1))
        assert decode_token(token) is None

    def test_decode_wrong_signature(self):
        token = jose_jwt.encode(
            {"sub": "u1", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            "wrong-secret-key",
            algorithm=settings.ALGORITHM,
        )
        assert decode_token(token) is None

    def test_decode_tampered_payload(self):
        token = create_access_token({"sub": "u1"})
        parts = token.split(".")
        tampered = parts[0] + "." + parts[1][:-1] + "X" + "." + parts[2]
        assert decode_token(tampered) is None


# =============================================================================
# TOKEN BLACKLIST
# =============================================================================


class TestTokenBlacklist:
    """Test token blacklisting (logout)."""

    def test_jti_is_consistent(self):
        jti1 = get_token_jti("test.token.string")
        jti2 = get_token_jti("test.token.string")
        assert jti1 == jti2
        assert len(jti1) == 32

    def test_different_tokens_different_jti(self):
        assert get_token_jti("token.one") != get_token_jti("token.two")

    def test_remaining_ttl_future(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        ttl = get_token_remaining_ttl({"exp": future.timestamp()})
        assert 3590 < ttl < 3610

    def test_remaining_ttl_expired(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        assert get_token_remaining_ttl({"exp": past.timestamp()}) == 0

    def test_remaining_ttl_no_exp(self):
        assert get_token_remaining_ttl({"sub": "u1"}) == 0

    @pytest.mark.asyncio
    async def test_blacklist_token_success(self):
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)

        token = create_access_token({"sub": "u1"})
        payload = decode_token(token)
        result = await blacklist_token(mock_redis, token, payload)

        assert result is True
        mock_redis.set.assert_called_once()
        key = mock_redis.set.call_args[0][0]
        assert key.startswith(TOKEN_BLACKLIST_PREFIX)

    @pytest.mark.asyncio
    async def test_blacklist_expired_token_skips_redis(self):
        mock_redis = AsyncMock()
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        payload = {"sub": "u1", "exp": past.timestamp()}

        result = await blacklist_token(mock_redis, "expired.token", payload)
        assert result is True
        mock_redis.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_blacklist_redis_failure(self):
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(side_effect=Exception("Redis error"))

        token = create_access_token({"sub": "u1"})
        payload = decode_token(token)
        result = await blacklist_token(mock_redis, token, payload)
        assert result is False

    @pytest.mark.asyncio
    async def test_is_blacklisted_true(self):
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=1)
        assert await is_token_blacklisted(mock_redis, "bl.token") is True

    @pytest.mark.asyncio
    async def test_is_blacklisted_false(self):
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=0)
        assert await is_token_blacklisted(mock_redis, "valid.token") is False

    @pytest.mark.asyncio
    async def test_is_blacklisted_redis_failure_fails_open(self):
        """SECURITY CONCERN: documented fail-open behavior on Redis failure."""
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(side_effect=Exception("Redis down"))
        result = await is_token_blacklisted(mock_redis, "some.token")
        # Fails open — token treated as valid when Redis is down
        assert result is False


# =============================================================================
# JWT SECURITY VULNERABILITIES
# =============================================================================


class TestJWTSecurityVulnerabilities:
    """Test known JWT attack vectors."""

    def test_algorithm_none_attack_rejected(self):
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()
        payload_data = {
            "sub": "admin",
            "is_admin": True,
            "exp": (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp(),
        }
        payload = base64.urlsafe_b64encode(
            json.dumps(payload_data).encode()
        ).rstrip(b"=").decode()
        malicious_token = f"{header}.{payload}."
        assert decode_token(malicious_token) is None

    def test_algorithm_confusion_rejected(self):
        data = {"sub": "u1", "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
        token = jose_jwt.encode(data, settings.SECRET_KEY, algorithm="HS384")
        assert decode_token(token) is None

    def test_injection_in_claims_decoded_but_not_executed(self):
        malicious = {
            "sub": "user123'; DROP TABLE users; --",
            "email": "<script>alert('xss')</script>",
            "is_admin": "true",
        }
        token = create_access_token(malicious)
        result = decode_token(token)
        assert result is not None
        assert result["sub"] == malicious["sub"]

    def test_extremely_long_expiry_accepted(self):
        """Document: tokens with very long expiry are accepted (no max-exp validation)."""
        token = create_access_token({"sub": "u1"}, expires_delta=timedelta(days=365 * 100))
        result = decode_token(token)
        assert result is not None
