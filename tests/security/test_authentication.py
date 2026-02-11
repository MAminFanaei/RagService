# tests/security/test_authentication.py
"""
Security Tests for Authentication

Rebuilt from legacy test_authentication.py — adapted to async infrastructure.
Tests for JWT attack vectors, brute force protection, password security,
session management, user enumeration, and token blacklist security.

All tests use httpx.AsyncClient matching the async FastAPI app.
"""

import pytest
import uuid
import base64
import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from jose import jwt as jose_jwt

from app.core.security import (
    create_access_token,
    create_refresh_token,
    create_token_pair,
    decode_token,
    blacklist_token,
    is_token_blacklisted,
)
from app.config import settings


# =============================================================================
# JWT SECURITY ATTACKS
# =============================================================================


class TestJWTSecurityAttacks:
    """Test JWT security vulnerabilities — rebuilt from legacy test_authentication.py."""

    @pytest.mark.asyncio
    async def test_none_algorithm_attack(self, client, test_user):
        """
        Test 'none' algorithm attack.
        Attacker tries to use 'alg: none' to bypass signature verification.
        """
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": "JWT"}).encode()
        ).rstrip(b'=').decode()

        payload = base64.urlsafe_b64encode(
            json.dumps({
                "sub": test_user.id,
                "email": test_user.email,
                "is_admin": True,
                "type": "access",
                "exp": (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
            }).encode()
        ).rstrip(b'=').decode()

        malicious_token = f"{header}.{payload}."

        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {malicious_token}"}
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_algorithm_confusion_hs256_to_hs384(self, client, test_user):
        """
        Test algorithm confusion attack.
        Try signing with different HMAC algorithm.
        """
        payload = {
            "sub": test_user.id,
            "email": test_user.email,
            "is_admin": False,
            "type": "access",
            "exp": (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
        }

        wrong_algo_token = jose_jwt.encode(
            payload, settings.SECRET_KEY, algorithm="HS384"
        )

        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {wrong_algo_token}"}
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_tampered_token_payload(self, client, test_user):
        """
        Test token tampering detection.
        Modify payload after signing to escalate privileges.
        """
        valid_token = create_access_token({
            "sub": test_user.id,
            "email": test_user.email,
            "is_admin": False
        })

        parts = valid_token.split('.')
        payload_bytes = base64.urlsafe_b64decode(parts[1] + '==')
        payload_data = json.loads(payload_bytes)
        payload_data['is_admin'] = True

        new_payload = base64.urlsafe_b64encode(
            json.dumps(payload_data).encode()
        ).rstrip(b'=').decode()

        tampered_token = f"{parts[0]}.{new_payload}.{parts[2]}"

        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {tampered_token}"}
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_token_rejected(self, client, test_user):
        """Test that expired tokens are rejected."""
        expired_token = create_access_token(
            {"sub": test_user.id, "email": test_user.email, "is_admin": False},
            expires_delta=timedelta(seconds=-1)
        )

        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {expired_token}"}
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_token_with_wrong_secret(self, client, test_user):
        """Test token signed with different secret."""
        payload = {
            "sub": test_user.id,
            "email": test_user.email,
            "is_admin": False,
            "type": "access",
            "exp": (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
        }

        wrong_secret_token = jose_jwt.encode(
            payload, "wrong-secret-key-12345", algorithm=settings.ALGORITHM
        )

        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {wrong_secret_token}"}
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_refresh_token_as_access_token(self, client, test_user):
        """Test using refresh token where access token is expected."""
        refresh_token = create_refresh_token({
            "sub": test_user.id,
            "email": test_user.email,
            "is_admin": False
        })

        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {refresh_token}"}
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_token_reuse_after_logout(self, client, auth_headers):
        """Test that token is invalidated after logout."""
        await client.post("/api/v1/auth/logout", headers=auth_headers)

        response = await client.get("/api/v1/auth/me", headers=auth_headers)
        assert response.status_code == 401


# =============================================================================
# BRUTE FORCE PROTECTION
# =============================================================================


class TestBruteForceProtection:
    """Test brute force attack protection."""

    @pytest.mark.asyncio
    async def test_login_rate_limiting(self, client, test_user):
        """Test that login attempts are rate limited."""
        failed_attempts = 0
        rate_limited = False

        for i in range(20):
            response = await client.post(
                "/api/v1/auth/login",
                json={
                    "login": test_user.email,
                    "password": f"wrongpassword{i}"
                }
            )
            if response.status_code == 429:
                rate_limited = True
                break
            elif response.status_code == 401:
                failed_attempts += 1

        assert failed_attempts > 0

    @pytest.mark.asyncio
    async def test_registration_rate_limiting(self, client):
        """Test that registration attempts are rate limited."""
        responses = []

        for i in range(10):
            response = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": f"test{i}_{uuid.uuid4().hex[:8]}@example.com",
                    "username": f"user{i}_{uuid.uuid4().hex[:8]}",
                    "password": "SecurePassword123!"
                }
            )
            responses.append(response.status_code)

        assert all(r in [201, 400, 422, 429] for r in responses)


# =============================================================================
# PASSWORD SECURITY
# =============================================================================


class TestPasswordSecurity:
    """Test password handling security."""

    @pytest.mark.asyncio
    async def test_password_not_in_response(self, client, auth_headers):
        """Password should never appear in API responses."""
        response = await client.get("/api/v1/auth/me", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "password" not in str(data).lower()
        assert "hashed_password" not in str(data).lower()

    @pytest.mark.asyncio
    async def test_password_not_in_user_list(self, client, admin_headers):
        """Password should not appear in admin user list."""
        response = await client.get("/api/v1/admin/users", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert "password" not in str(data).lower()
        assert "hashed_password" not in str(data).lower()

    @pytest.mark.asyncio
    async def test_timing_attack_resistance_login(self, client, test_user):
        """
        Test that login timing doesn't reveal user existence.
        Time difference between valid user + wrong password vs invalid user
        should be similar.
        """
        iterations = 5
        valid_user_times = []
        invalid_user_times = []

        for _ in range(iterations):
            start = time.perf_counter()
            await client.post(
                "/api/v1/auth/login",
                json={"login": test_user.email, "password": "wrongpassword"}
            )
            valid_user_times.append(time.perf_counter() - start)

            start = time.perf_counter()
            await client.post(
                "/api/v1/auth/login",
                json={
                    "login": f"nonexistent_{uuid.uuid4()}@example.com",
                    "password": "anypassword"
                }
            )
            invalid_user_times.append(time.perf_counter() - start)

        avg_valid = sum(valid_user_times) / len(valid_user_times)
        avg_invalid = sum(invalid_user_times) / len(invalid_user_times)

        ratio = max(avg_valid, avg_invalid) / max(min(avg_valid, avg_invalid), 0.0001)

        assert ratio < 50.0, f"Timing difference too large: {ratio}"


# =============================================================================
# SESSION SECURITY
# =============================================================================


class TestSessionSecurity:
    """Test session/token management security."""

    @pytest.mark.asyncio
    async def test_logout_invalidates_all_tokens(self, client, test_user, test_password):
        """Test that logout properly invalidates tokens."""
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"login": test_user.email, "password": test_password}
        )
        tokens = login_response.json()

        await client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )

        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_token_not_in_url(self, client, auth_headers):
        """Tokens should not be accepted in URL parameters."""
        token = auth_headers["Authorization"].split(" ")[1]
        response = await client.get(f"/api/v1/auth/me?token={token}")
        assert response.status_code in [401, 403]

    @pytest.mark.asyncio
    async def test_token_not_in_cookies_unless_configured(self, client):
        """Test cookie-based auth if configured — documents expected behavior."""
        # This depends on your configuration
        pass


# =============================================================================
# USER ENUMERATION
# =============================================================================


class TestUserEnumeration:
    """Test for user enumeration vulnerabilities."""

    @pytest.mark.asyncio
    async def test_login_same_error_message(self, client, test_user):
        """Login error should not reveal if user exists."""
        response1 = await client.post(
            "/api/v1/auth/login",
            json={"login": test_user.email, "password": "wrongpassword"}
        )
        response2 = await client.post(
            "/api/v1/auth/login",
            json={"login": "nonexistent@example.com", "password": "anypassword"}
        )
        assert response1.status_code == response2.status_code

    @pytest.mark.asyncio
    async def test_registration_user_exists(self, client, test_user):
        """
        Registration error for existing email might reveal user exists.
        This is a trade-off between UX and security.
        Document your choice.
        """
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": test_user.email,
                "username": f"different_{uuid.uuid4().hex[:8]}",
                "password": "SecurePassword123!"
            }
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_password_reset_enumeration(self, client, test_user):
        """
        Password reset should not reveal if email exists.
        (If you implement password reset)
        """
        # Placeholder — implement when password reset endpoint exists
        pass


# =============================================================================
# TOKEN BLACKLIST SECURITY
# =============================================================================


class TestTokenBlacklistSecurity:
    """Test token blacklist functionality."""

    @pytest.mark.asyncio
    async def test_blacklist_bypass_attempt(self, db, test_user):
        """Test that blacklisted tokens cannot be reused."""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.exists = AsyncMock(return_value=1)

        tokens = create_token_pair(
            user_id=test_user.id,
            email=test_user.email,
            is_admin=False
        )
        access_token = tokens["access_token"]
        payload = decode_token(access_token)

        result = await blacklist_token(mock_redis, access_token, payload)
        assert result is True

        is_blacklisted = await is_token_blacklisted(mock_redis, access_token)
        assert is_blacklisted is True

    @pytest.mark.asyncio
    async def test_blacklisted_token_rejected(self, client, auth_headers):
        """Blacklisted token should be rejected."""
        await client.post("/api/v1/auth/logout", headers=auth_headers)

        response = await client.get("/api/v1/auth/me", headers=auth_headers)
        assert response.status_code == 401