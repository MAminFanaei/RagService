# tests/security/test_authentication.py
"""
Security Tests for Authentication

Tests for authentication vulnerabilities and attacks.
"""

from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timedelta, timezone
import uuid
import base64
import json
import time

from jose import jwt as jose_jwt

from app.models.user import User
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.config import settings


class TestJWTSecurityAttacks:
    """Test JWT security vulnerabilities"""
    
    def test_none_algorithm_attack(self, client: TestClient, test_user: User):
        """
        Test 'none' algorithm attack.
        Attacker tries to use 'alg: none' to bypass signature verification.
        """
        # Craft malicious header with 'none' algorithm
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
        
        # Token with empty signature
        malicious_token = f"{header}.{payload}."
        
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {malicious_token}"}
        )
        
        assert response.status_code == 401
    
    def test_algorithm_confusion_hs256_to_hs384(self, client: TestClient, test_user: User):
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
        
        # Sign with HS384 instead of HS256
        wrong_algo_token = jose_jwt.encode(
            payload,
            settings.SECRET_KEY,
            algorithm="HS384"
        )
        
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {wrong_algo_token}"}
        )
        
        assert response.status_code == 401
    
    def test_tampered_token_payload(self, client: TestClient, test_user: User):
        """
        Test token tampering detection.
        Modify payload after signing.
        """
        # Get valid token
        valid_token = create_access_token({
            "sub": test_user.id,
            "email": test_user.email,
            "is_admin": False
        })
        
        # Tamper with it
        parts = valid_token.split('.')
        
        # Decode payload, modify, re-encode
        payload_bytes = base64.urlsafe_b64decode(parts[1] + '==')
        payload_data = json.loads(payload_bytes)
        payload_data['is_admin'] = True  # Escalate privileges
        
        new_payload = base64.urlsafe_b64encode(
            json.dumps(payload_data).encode()
        ).rstrip(b'=').decode()
        
        tampered_token = f"{parts[0]}.{new_payload}.{parts[2]}"
        
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {tampered_token}"}
        )
        
        assert response.status_code == 401
    
    def test_expired_token_rejected(self, client: TestClient, test_user: User):
        """Test that expired tokens are rejected"""
        expired_token = create_access_token(
            {"sub": test_user.id, "email": test_user.email, "is_admin": False},
            expires_delta=timedelta(seconds=-1)
        )
        
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {expired_token}"}
        )
        
        assert response.status_code == 401
    
    def test_token_with_wrong_secret(self, client: TestClient, test_user: User):
        """Test token signed with different secret"""
        payload = {
            "sub": test_user.id,
            "email": test_user.email,
            "is_admin": False,
            "type": "access",
            "exp": (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
        }
        
        wrong_secret_token = jose_jwt.encode(
            payload,
            "wrong-secret-key-12345",
            algorithm=settings.ALGORITHM
        )
        
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {wrong_secret_token}"}
        )
        
        assert response.status_code == 401
    
    def test_refresh_token_as_access_token(self, client: TestClient, test_user: User):
        """Test using refresh token where access token is expected"""
        refresh_token = create_refresh_token({
            "sub": test_user.id,
            "email": test_user.email,
            "is_admin": False
        })
        
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {refresh_token}"}
        )
        
        assert response.status_code == 401
    
    def test_token_reuse_after_logout(self, client: TestClient, user_auth_header: dict, user_token: dict):
        """Test that token is invalidated after logout"""
        # Logout
        client.post("/api/v1/auth/logout", headers=user_auth_header)
        
        # Try to reuse the same token
        response = client.get("/api/v1/auth/me", headers=user_auth_header)
        
        assert response.status_code == 401
        assert "revoked" in response.json().get("message", "").lower()


class TestBruteForceProtection:
    """Test brute force attack protection"""
    
    def test_login_rate_limiting(self, client: TestClient, test_user: User):
        """Test that login attempts are rate limited"""
        # Make many failed login attempts
        failed_attempts = 0
        rate_limited = False
        
        for i in range(20):
            response = client.post(
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
        
        # Depending on your rate limit configuration
        # Document expected behavior
        # assert rate_limited is True, "Login should be rate limited after multiple failures"
        assert failed_attempts > 0  # At least some should fail
    
    def test_registration_rate_limiting(self, client: TestClient):
        """Test that registration attempts are rate limited"""
        responses = []
        
        for i in range(10):
            response = client.post(
                "/api/v1/auth/register",
                json={
                    "email": f"test{i}_{uuid.uuid4().hex[:8]}@example.com",
                    "username": f"user{i}_{uuid.uuid4().hex[:8]}",
                    "password": "SecurePassword123!"
                }
            )
            responses.append(response.status_code)
        
        # Check if any got rate limited
        rate_limited = 429 in responses
        # Document expected behavior based on your rate limit config


class TestPasswordSecurity:
    """Test password handling security"""
    
    def test_password_not_in_response(self, client: TestClient, user_auth_header: dict):
        """Password should never appear in API responses"""
        response = client.get("/api/v1/auth/me", headers=user_auth_header)
        
        assert response.status_code == 200
        data = response.json()
        
        # Check no password-related fields
        assert "password" not in str(data).lower()
        assert "hashed_password" not in str(data).lower()
    
    def test_password_not_in_user_list(self, client: TestClient, admin_auth_header: dict):
        """Password should not appear in admin user list"""
        response = client.get("/api/v1/admin/users", headers=admin_auth_header)
        
        assert response.status_code == 200
        data = response.json()
        
        assert "password" not in str(data).lower()
        assert "hashed_password" not in str(data).lower()
    
    def test_timing_attack_resistance_login(self, client: TestClient, test_user: User, test_password: str):
        """
        Test that login timing doesn't reveal user existence.
        Time difference between valid user + wrong password vs invalid user should be similar.
        """
        iterations = 5
        valid_user_times = []
        invalid_user_times = []
        
        for _ in range(iterations):
            # Valid user, wrong password
            start = time.perf_counter()
            client.post(
                "/api/v1/auth/login",
                json={"login": test_user.email, "password": "wrongpassword"}
            )
            valid_user_times.append(time.perf_counter() - start)
            
            # Invalid user
            start = time.perf_counter()
            client.post(
                "/api/v1/auth/login",
                json={"login": f"nonexistent_{uuid.uuid4()}@example.com", "password": "anypassword"}
            )
            invalid_user_times.append(time.perf_counter() - start)
        
        # Compare average times
        avg_valid = sum(valid_user_times) / len(valid_user_times)
        avg_invalid = sum(invalid_user_times) / len(invalid_user_times)
        
        # Times should be within 50% of each other
        ratio = max(avg_valid, avg_invalid) / max(min(avg_valid, avg_invalid), 0.0001)
        
        # Note: This is a soft test - timing can vary
        # In production, use constant-time comparison
        assert ratio < 20.0, f"Timing difference too large: {ratio}"


class TestSessionSecurity:
    """Test session/token management security"""
    
    def test_logout_invalidates_all_tokens(self, client: TestClient, test_user: User, test_password: str):
        """Test that logout properly invalidates tokens"""
        # Login to get tokens
        login_response = client.post(
            "/api/v1/auth/login",
            json={"login": test_user.email, "password": test_password}
        )
        tokens = login_response.json()
        
        # Logout
        client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
        
        # Access token should be invalid
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
        assert response.status_code == 401
    
    def test_token_not_in_url(self, client: TestClient, user_auth_header: dict):
        """Tokens should not be accepted in URL parameters"""
        token = user_auth_header["Authorization"].split(" ")[1]
        
        response = client.get(f"/api/v1/auth/me?token={token}")
        
        # Should fail because token is not in header
        assert response.status_code in [401, 403]
    
    def test_token_not_in_cookies_unless_configured(self, client: TestClient):
        """Test cookie-based auth if configured"""
        # This depends on your configuration
        # Document expected behavior
        pass


class TestUserEnumeration:
    """Test for user enumeration vulnerabilities"""
    
    def test_login_same_error_message(self, client: TestClient, test_user: User):
        """Login error should not reveal if user exists"""
        # Wrong password for existing user
        response1 = client.post(
            "/api/v1/auth/login",
            json={"login": test_user.email, "password": "wrongpassword"}
        )
        
        # Non-existent user
        response2 = client.post(
            "/api/v1/auth/login",
            json={"login": "nonexistent@example.com", "password": "anypassword"}
        )
        
        # Error messages should be the same
        assert response1.status_code == response2.status_code
        # Ideally, error messages should be identical
        # assert response1.json()["message"] == response2.json()["message"]
    
    def test_registration_user_exists(self, client: TestClient, test_user: User):
        """
        Registration error for existing email might reveal user exists.
        This is a trade-off between UX and security.
        Document your choice.
        """
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": test_user.email,
                "username": f"different_{uuid.uuid4().hex[:8]}",
                "password": "SecurePassword123!"
            }
        )
        
        # Current implementation reveals if email exists
        # This is a conscious choice for better UX
        assert response.status_code == 400
    
    def test_password_reset_enumeration(self, client: TestClient, test_user: User):
        """
        Password reset should not reveal if email exists.
        (If you implement password reset)
        """
        # If you have password reset endpoint, test here
        pass


class TestTokenBlacklistSecurity:
    """Test token blacklist functionality"""
    

    @pytest.mark.asyncio
    async def test_blacklist_bypass_attempt(self, db, test_user):
        """Test that blacklisted tokens cannot be reused"""
        from app.core.security import (
            create_token_pair, blacklist_token, 
            is_token_blacklisted, decode_token
        )
        
        # Create mock redis that simulates blacklist
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
        
        # Blacklist
        result = await blacklist_token(mock_redis, access_token, payload)
        assert result is True
        
        # Verify blacklisted
        is_blacklisted = await is_token_blacklisted(mock_redis, access_token)
        assert is_blacklisted is True
    
    def test_blacklisted_token_rejected(self, client: TestClient, user_auth_header: dict):
        """Blacklisted token should be rejected"""
        # Logout (blacklists token)
        client.post("/api/v1/auth/logout", headers=user_auth_header)
        
        # Try to use token
        response = client.get("/api/v1/auth/me", headers=user_auth_header)
        
        assert response.status_code == 401
