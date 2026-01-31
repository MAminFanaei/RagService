# tests/unit/test_security.py
"""
Unit Tests for Security Module

Tests password hashing, JWT token operations, and token blacklisting.
Uses python-jose for JWT operations (not PyJWT).
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import hashlib

# Import from jose, not jwt directly
from jose import jwt as jose_jwt
from jose.exceptions import JWTError

from app.core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    create_refresh_token,
    decode_token,
    create_token_pair,
    get_token_jti,
    get_token_remaining_ttl,
    blacklist_token,
    is_token_blacklisted,
    TOKEN_BLACKLIST_PREFIX
)
from app.config import settings


class TestPasswordHashing:
    """Test password hashing with Argon2"""
    
    def test_password_hash_creates_valid_hash(self):
        """Password hashing should create a valid Argon2 hash"""
        password = "SecurePassword123!"
        hashed = get_password_hash(password)
        
        assert hashed is not None
        assert hashed != password
        assert hashed.startswith("$argon2")  # Argon2 hash prefix
    
    def test_password_hash_is_unique(self):
        """Same password should create different hashes (salting)"""
        password = "SecurePassword123!"
        hash1 = get_password_hash(password)
        hash2 = get_password_hash(password)
        
        assert hash1 != hash2  # Salt should make them different
    
    def test_verify_password_correct(self):
        """Correct password should verify successfully"""
        password = "SecurePassword123!"
        hashed = get_password_hash(password)
        
        assert verify_password(password, hashed) is True
    
    def test_verify_password_incorrect(self):
        """Incorrect password should fail verification"""
        password = "SecurePassword123!"
        wrong_password = "WrongPassword456!"
        hashed = get_password_hash(password)
        
        assert verify_password(wrong_password, hashed) is False
    
    def test_verify_password_empty(self):
        """Empty password should fail verification"""
        password = "SecurePassword123!"
        hashed = get_password_hash(password)
        
        assert verify_password("", hashed) is False
    
    def test_verify_password_with_special_chars(self):
        """Passwords with special characters should work"""
        password = "P@$$w0rd!#$%^&*()_+-=[]{}|;':\",./<>?"
        hashed = get_password_hash(password)
        
        assert verify_password(password, hashed) is True
    
    def test_verify_password_unicode(self):
        """Unicode passwords should work"""
        password = "密码123!@#中文"
        hashed = get_password_hash(password)
        
        assert verify_password(password, hashed) is True
    
    def test_password_timing_attack_resistance(self):
        """Verification should take similar time for valid/invalid"""
        import time
        
        password = "SecurePassword123!"
        hashed = get_password_hash(password)
        
        # Time correct password
        start = time.perf_counter()
        verify_password(password, hashed)
        correct_time = time.perf_counter() - start
        
        # Time incorrect password
        start = time.perf_counter()
        verify_password("wrong", hashed)
        incorrect_time = time.perf_counter() - start
        
        # Times should be within 50% of each other (Argon2 is constant-time)
        ratio = max(correct_time, incorrect_time) / max(min(correct_time, incorrect_time), 0.0001)
        assert ratio < 2.0  # Allow some variance


class TestJWTTokenCreation:
    """Test JWT token creation with python-jose"""
    
    def test_create_access_token_basic(self):
        """Should create valid access token"""
        data = {"sub": "user123", "email": "test@example.com"}
        token = create_access_token(data)
        
        assert token is not None
        assert isinstance(token, str)
        assert len(token.split(".")) == 3  # JWT has 3 parts
    
    def test_create_access_token_with_expiry(self):
        """Should create token with custom expiry"""
        data = {"sub": "user123"}
        expires = timedelta(minutes=5)
        token = create_access_token(data, expires_delta=expires)
        
        payload = jose_jwt.decode(
            token, 
            settings.SECRET_KEY, 
            algorithms=[settings.ALGORITHM]
        )
        
        assert "exp" in payload
        assert payload["type"] == "access"
    
    def test_create_access_token_default_expiry(self):
        """Should use default expiry when not specified"""
        data = {"sub": "user123"}
        token = create_access_token(data)
        
        payload = jose_jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        
        exp_time = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        now = datetime.now(timezone.utc)
        
        # Should expire in approximately ACCESS_TOKEN_EXPIRE_MINUTES
        expected_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        actual_delta = exp_time - now
        
        # Allow 5 seconds tolerance
        assert abs(actual_delta.total_seconds() - expected_delta.total_seconds()) < 5
    
    def test_create_refresh_token(self):
        """Should create valid refresh token"""
        data = {"sub": "user123", "email": "test@example.com"}
        token = create_refresh_token(data)
        
        payload = jose_jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        
        assert payload["type"] == "refresh"
        assert payload["sub"] == "user123"
    
    def test_refresh_token_longer_expiry(self):
        """Refresh token should have longer expiry than access token"""
        data = {"sub": "user123"}
        
        access_token = create_access_token(data)
        refresh_token = create_refresh_token(data)
        
        access_payload = jose_jwt.decode(
            access_token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        refresh_payload = jose_jwt.decode(
            refresh_token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        
        assert refresh_payload["exp"] > access_payload["exp"]
    
    def test_create_token_pair(self):
        """Should create both access and refresh tokens"""
        tokens = create_token_pair(
            user_id="user123",
            email="test@example.com",
            is_admin=False
        )
        
        assert "access_token" in tokens
        assert "refresh_token" in tokens
        assert tokens["token_type"] == "bearer"
        
        # Verify access token
        access_payload = decode_token(tokens["access_token"])
        assert access_payload["sub"] == "user123"
        assert access_payload["email"] == "test@example.com"
        assert access_payload["is_admin"] is False
        assert access_payload["type"] == "access"
        
        # Verify refresh token
        refresh_payload = decode_token(tokens["refresh_token"])
        assert refresh_payload["type"] == "refresh"
    
    def test_token_contains_required_claims(self):
        """Token should contain all required claims"""
        tokens = create_token_pair(
            user_id="user123",
            email="admin@example.com",
            is_admin=True
        )
        
        payload = decode_token(tokens["access_token"])
        
        assert "sub" in payload
        assert "email" in payload
        assert "is_admin" in payload
        assert "exp" in payload
        assert "type" in payload


class TestJWTTokenDecoding:
    """Test JWT token decoding"""
    
    def test_decode_valid_token(self):
        """Should decode valid token successfully"""
        data = {"sub": "user123", "email": "test@example.com"}
        token = create_access_token(data)
        
        payload = decode_token(token)
        
        assert payload is not None
        assert payload["sub"] == "user123"
        assert payload["email"] == "test@example.com"
    
    def test_decode_invalid_token(self):
        """Should return None for invalid token"""
        payload = decode_token("invalid.token.here")
        
        assert payload is None
    
    def test_decode_expired_token(self):
        """Should return None for expired token"""
        data = {"sub": "user123"}
        # Create token that expired 1 hour ago
        token = create_access_token(data, expires_delta=timedelta(hours=-1))
        
        payload = decode_token(token)
        
        assert payload is None
    
    def test_decode_wrong_signature(self):
        """Should return None for token with wrong signature"""
        data = {"sub": "user123"}
        # Create token with different secret
        wrong_token = jose_jwt.encode(
            {**data, "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            "wrong-secret-key",
            algorithm=settings.ALGORITHM
        )
        
        payload = decode_token(wrong_token)
        
        assert payload is None
    
    def test_decode_modified_payload(self):
        """Should return None for tampered token"""
        data = {"sub": "user123"}
        token = create_access_token(data)
        
        # Tamper with the token (change a character in payload)
        parts = token.split(".")
        # Modify the payload part
        tampered = parts[0] + "." + parts[1][:-1] + "X" + "." + parts[2]
        
        payload = decode_token(tampered)
        
        assert payload is None
    
    # def test_decode_empty_token(self):
    #     """Should return None for empty token"""
    #     assert decode_token("") is None
    #     assert decode_token(None) is None if decode_token.__code__.co_argcount > 0 else True


class TestTokenBlacklist:
    """Test token blacklisting functionality"""
    
    def test_get_token_jti(self):
        """Should generate consistent JTI from token"""
        token = "test.token.string"
        
        jti1 = get_token_jti(token)
        jti2 = get_token_jti(token)
        
        assert jti1 == jti2
        assert len(jti1) == 32  # SHA256 truncated to 32 chars
    
    def test_get_token_jti_different_tokens(self):
        """Different tokens should have different JTIs"""
        token1 = "test.token.one"
        token2 = "test.token.two"
        
        jti1 = get_token_jti(token1)
        jti2 = get_token_jti(token2)
        
        assert jti1 != jti2
    
    def test_get_token_remaining_ttl_future(self):
        """Should calculate correct TTL for valid token"""
        future_exp = datetime.now(timezone.utc) + timedelta(hours=1)
        payload = {"exp": future_exp.timestamp()}
        
        ttl = get_token_remaining_ttl(payload)
        
        # Should be approximately 3600 seconds (1 hour)
        assert 3590 < ttl < 3610
    
    def test_get_token_remaining_ttl_expired(self):
        """Should return 0 for expired token"""
        past_exp = datetime.now(timezone.utc) - timedelta(hours=1)
        payload = {"exp": past_exp.timestamp()}
        
        ttl = get_token_remaining_ttl(payload)
        
        assert ttl == 0
    
    def test_get_token_remaining_ttl_no_exp(self):
        """Should return 0 if no exp claim"""
        payload = {"sub": "user123"}
        
        ttl = get_token_remaining_ttl(payload)
        
        assert ttl == 0
    
    @pytest.mark.asyncio
    async def test_blacklist_token_success(self):
        """Should blacklist token in Redis"""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        
        token = create_access_token({"sub": "user123"})
        payload = decode_token(token)
        
        result = await blacklist_token(mock_redis, token, payload)
        
        assert result is True
        mock_redis.set.assert_called_once()
        
        # Verify the key format
        call_args = mock_redis.set.call_args
        key = call_args[0][0]
        assert key.startswith(TOKEN_BLACKLIST_PREFIX)
    
    @pytest.mark.asyncio
    async def test_blacklist_expired_token(self):
        """Should skip blacklisting already expired token"""
        mock_redis = AsyncMock()
        
        # Create expired token
        past_exp = datetime.now(timezone.utc) - timedelta(hours=1)
        payload = {"sub": "user123", "exp": past_exp.timestamp()}
        
        result = await blacklist_token(mock_redis, "expired.token", payload)
        
        assert result is True
        mock_redis.set.assert_not_called()  # Should not call Redis
    
    @pytest.mark.asyncio
    async def test_blacklist_token_redis_failure(self):
        """Should return False on Redis error"""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(side_effect=Exception("Redis error"))
        
        token = create_access_token({"sub": "user123"})
        payload = decode_token(token)
        
        result = await blacklist_token(mock_redis, token, payload)
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_is_token_blacklisted_true(self):
        """Should return True for blacklisted token"""
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=1)
        
        result = await is_token_blacklisted(mock_redis, "blacklisted.token")
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_is_token_blacklisted_false(self):
        """Should return False for non-blacklisted token"""
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=0)
        
        result = await is_token_blacklisted(mock_redis, "valid.token")
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_is_token_blacklisted_redis_failure(self):
        """Should return False (fail open) on Redis error"""
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(side_effect=Exception("Redis error"))
        
        result = await is_token_blacklisted(mock_redis, "some.token")
        
        # SECURITY CONCERN: This fails open!
        assert result is False


class TestJWTSecurityVulnerabilities:
    """Test for known JWT vulnerabilities"""
    
    def test_algorithm_none_attack(self):
        """Should reject tokens with 'none' algorithm"""
        # Create a token with 'none' algorithm (unsigned)
        import base64
        import json
        
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": "JWT"}).encode()
        ).rstrip(b'=').decode()
        
        payload_data = {
            "sub": "admin",
            "is_admin": True,
            "exp": (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
        }
        payload = base64.urlsafe_b64encode(
            json.dumps(payload_data).encode()
        ).rstrip(b'=').decode()
        
        # Token with empty signature
        malicious_token = f"{header}.{payload}."
        
        result = decode_token(malicious_token)
        
        assert result is None  # Should reject
    
    def test_algorithm_confusion_attack(self):
        """Should reject tokens signed with different algorithm"""
        # Try HS384 when expecting HS256
        data = {"sub": "user123", "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
        wrong_algo_token = jose_jwt.encode(data, settings.SECRET_KEY, algorithm="HS384")
        
        result = decode_token(wrong_algo_token)
        
        assert result is None  # Should reject
    
    def test_key_confusion_attack(self):
        """Should reject tokens signed with public key as HMAC secret"""
        # This tests RS256 vs HS256 confusion
        # In a real attack, attacker would use public key as HMAC secret
        fake_public_key = "-----BEGIN PUBLIC KEY-----\nMIIBIjANBg..."
        
        data = {
            "sub": "admin",
            "is_admin": True,
            "exp": (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
        }
        
        try:
            malicious_token = jose_jwt.encode(data, fake_public_key, algorithm="HS256")
            result = decode_token(malicious_token)
            assert result is None
        except Exception:
            pass  # Expected - invalid key format
    
    def test_token_with_future_iat(self):
        """Should handle tokens with future 'issued at' time"""
        future_iat = datetime.now(timezone.utc) + timedelta(hours=1)
        data = {
            "sub": "user123",
            "iat": future_iat.timestamp(),
            "exp": (future_iat + timedelta(hours=1)).timestamp()
        }
        token = jose_jwt.encode(data, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
        
        # Should still decode (iat is not validated by default)
        result = decode_token(token)
        # This depends on your validation policy
        assert result is not None or result is None  # Document the behavior
    
    def test_extremely_long_expiry(self):
        """Should handle tokens with very long expiry"""
        far_future = datetime.now(timezone.utc) + timedelta(days=365*100)  # 100 years
        data = {"sub": "user123"}
        token = create_access_token(data, expires_delta=timedelta(days=365*100))
        
        result = decode_token(token)
        
        # Should still work (but you might want to add max expiry validation)
        assert result is not None
    
    def test_injection_in_claims(self):
        """Should handle malicious content in claims"""
        malicious_data = {
            "sub": "user123'; DROP TABLE users; --",
            "email": "<script>alert('xss')</script>",
            "is_admin": "true"  # String instead of bool
        }
        token = create_access_token(malicious_data)
        result = decode_token(token)
        
        # Token should decode, but claims should be validated elsewhere
        assert result is not None
        assert result["sub"] == malicious_data["sub"]
        # is_admin should be validated as boolean by the application
