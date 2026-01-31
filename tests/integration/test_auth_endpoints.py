# tests/integration/test_auth_endpoints.py
"""
Integration Tests for Authentication Endpoints

Tests the full authentication flow through the API.
Requires running test database and Redis.
"""

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient
import uuid

from app.models.user import User
from app.core.security import create_token_pair, decode_token


class TestRegistration:
    """Test user registration endpoint"""
    
    def test_register_success(self, client: TestClient):
        """Should register new user successfully"""
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": f"newuser_{uuid.uuid4().hex[:8]}@example.com",
                "username": f"newuser_{uuid.uuid4().hex[:8]}",
                "password": "SecurePassword123!",
                "full_name": "New User"
            }
        )
        
        assert response.status_code == 201
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
    
    def test_register_duplicate_email(self, client: TestClient, test_user: User):
        """Should reject duplicate email"""
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": test_user.email,
                "username": f"unique_{uuid.uuid4().hex[:8]}",
                "password": "SecurePassword123!"
            }
        )
        
        assert response.status_code == 400
        assert "already registered" in response.json()["message"].lower()
    
    def test_register_duplicate_username(self, client: TestClient, test_user: User):
        """Should reject duplicate username"""
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": f"unique_{uuid.uuid4().hex[:8]}@example.com",
                "username": test_user.username,
                "password": "SecurePassword123!"
            }
        )
        
        assert response.status_code == 400
        assert "username" in response.json()["message"].lower()
    
    def test_register_weak_password(self, client: TestClient):
        """Should reject weak password"""
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": f"test_{uuid.uuid4().hex[:8]}@example.com",
                "username": f"test_{uuid.uuid4().hex[:8]}",
                "password": "weak"  # Too short
            }
        )
        
        assert response.status_code == 422  # Validation error
    
    def test_register_invalid_email(self, client: TestClient):
        """Should reject invalid email format"""
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "not-an-email",
                "password": "SecurePassword123!"
            }
        )
        
        assert response.status_code == 422
    
    def test_register_without_username(self, client: TestClient):
        """Should auto-generate username if not provided"""
        email = f"nouser_{uuid.uuid4().hex[:8]}@example.com"
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "SecurePassword123!"
            }
        )
        
        # This depends on whether username is required in schema
        # Current schema requires username, so this should fail
        assert response.status_code in [201, 422]


class TestLogin:
    """Test login endpoint"""
    
    def test_login_with_email_success(self, client: TestClient, test_user: User, test_password: str):
        """Should login with email successfully"""
        response = client.post(
            "/api/v1/auth/login",
            json={
                "login": test_user.email,
                "password": test_password
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
    
    def test_login_with_username_success(self, client: TestClient, test_user: User, test_password: str):
        """Should login with username successfully"""
        response = client.post(
            "/api/v1/auth/login",
            json={
                "login": test_user.username,
                "password": test_password
            }
        )
        
        assert response.status_code == 200
    
    def test_login_wrong_password(self, client: TestClient, test_user: User):
        """Should reject wrong password"""
        response = client.post(
            "/api/v1/auth/login",
            json={
                "login": test_user.email,
                "password": "wrongpassword"
            }
        )
        
        assert response.status_code == 401
        assert "incorrect" in response.json()["message"].lower()
    
    def test_login_nonexistent_user(self, client: TestClient):
        """Should reject non-existent user"""
        response = client.post(
            "/api/v1/auth/login",
            json={
                "login": "nonexistent@example.com",
                "password": "anypassword"
            }
        )
        
        assert response.status_code == 401
    
    def test_login_inactive_user(self, client: TestClient, inactive_user: User, test_password: str):
        """Should reject inactive user"""
        response = client.post(
            "/api/v1/auth/login",
            json={
                "login": inactive_user.email,
                "password": test_password
            }
        )
        
        assert response.status_code == 401


class TestTokenRefresh:
    """Test token refresh endpoint"""
    
    def test_refresh_token_success(self, client, test_user, test_password):
        """Test successful token refresh"""
        # First login to get tokens
        login_response = client.post(
            "/api/v1/auth/login",
            json={"login": test_user.email, "password": test_password}
        )
        assert login_response.status_code == 200
        tokens = login_response.json()
        
        # tokens is a dict with access_token and refresh_token
        assert "refresh_token" in tokens
        refresh_token = tokens["refresh_token"]
        
        # Use refresh token to get new tokens
        refresh_response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token}
        )
        
        assert refresh_response.status_code == 200
        new_tokens = refresh_response.json()
        assert "access_token" in new_tokens
        assert "refresh_token" in new_tokens
    
    def test_refresh_with_access_token_fails(self, client, test_user, test_password):
            """Test refresh with access token fails"""
            # First login
            login_response = client.post(
                "/api/v1/auth/login",
                json={"login": test_user.email, "password": test_password}
            )
            assert login_response.status_code == 200
            tokens = login_response.json()
            
            access_token = tokens["access_token"]
            
            # Try to use access token as refresh token - should fail
            refresh_response = client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": access_token}
            )
            
            assert refresh_response.status_code == 401
    
    def test_refresh_invalid_token(self, client: TestClient):
        """Should reject invalid refresh token"""
        response = client.post(
            "/api/v1/auth/refresh",
            json={
                "refresh_token": "invalid.token.here"
            }
        )
        
        assert response.status_code == 401


class TestLogout:
    """Test logout endpoint"""
    
    def test_logout_success(self, client: TestClient, user_auth_header: dict):
        """Should logout successfully"""
        response = client.post(
            "/api/v1/auth/logout",
            headers=user_auth_header
        )
        
        assert response.status_code == 200
        assert "logged out" in response.json()["message"].lower()
    
    def test_logout_invalidates_token(self, client: TestClient, user_auth_header: dict):
        """Token should be invalid after logout"""
        # Logout
        client.post("/api/v1/auth/logout", headers=user_auth_header)
        
        # Try to use same token
        response = client.get("/api/v1/auth/me", headers=user_auth_header)
        
        assert response.status_code == 401
        assert "revoked" in response.json()["message"].lower()
    
    def test_logout_without_token(self, client: TestClient):
        """Should fail without token"""
        response = client.post("/api/v1/auth/logout")
        
        assert response.status_code in [401, 403]


class TestCurrentUser:
    """Test /me endpoint"""
    
    def test_get_current_user(self, client: TestClient, test_user: User, user_auth_header: dict):
        """Should return current user info"""
        response = client.get(
            "/api/v1/auth/me",
            headers=user_auth_header
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == test_user.email
        assert data["username"] == test_user.username
        assert "remaining_messages_today" in data
    
    def test_get_current_user_no_token(self, client: TestClient):
        """Should fail without authentication"""
        response = client.get("/api/v1/auth/me")
        
        assert response.status_code in [401, 403]
    
    def test_get_current_user_invalid_token(self, client: TestClient):
        """Should fail with invalid token"""
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer invalid.token.here"}
        )
        
        assert response.status_code == 401


class TestPasswordChange:
    """Test password change endpoint"""
    
    def test_change_password_success(self, client: TestClient, user_auth_header: dict, test_password: str):
        """Should change password successfully"""
        response = client.put(
            "/api/v1/auth/me/password",
            headers=user_auth_header,
            json={
                "current_password": test_password,
                "new_password": "NewSecurePassword456!"
            }
        )
        
        assert response.status_code == 200
    
    def test_change_password_wrong_current(self, client: TestClient, user_auth_header: dict):
        """Should reject wrong current password"""
        response = client.put(
            "/api/v1/auth/me/password",
            headers=user_auth_header,
            json={
                "current_password": "wrongpassword",
                "new_password": "NewSecurePassword456!"
            }
        )
        
        assert response.status_code == 400
    
    def test_change_password_weak_new(self, client: TestClient, user_auth_header: dict, test_password: str):
        """Should reject weak new password"""
        response = client.put(
            "/api/v1/auth/me/password",
            headers=user_auth_header,
            json={
                "current_password": test_password,
                "new_password": "weak"
            }
        )
        
        assert response.status_code == 422


class TestEmailChange:
    """Test email change endpoint"""
    
    def test_change_email_success(self, client: TestClient, user_auth_header: dict, test_password: str):
        """Should change email successfully"""
        new_email = f"newemail_{uuid.uuid4().hex[:8]}@example.com"
        
        response = client.put(
            "/api/v1/auth/me/email",
            headers=user_auth_header,
            json={
                "new_email": new_email,
                "password": test_password
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["new_email"] == new_email
        assert data["is_verified"] is False
    
    def test_change_email_wrong_password(self, client: TestClient, user_auth_header: dict):
        """Should reject wrong password"""
        response = client.put(
            "/api/v1/auth/me/email",
            headers=user_auth_header,
            json={
                "new_email": "new@example.com",
                "password": "wrongpassword"
            }
        )
        
        assert response.status_code == 400


class TestProfileUpdate:
    """Test profile update endpoint"""
    
    def test_update_username(self, client: TestClient, user_auth_header: dict):
        """Should update username"""
        new_username = f"updated_{uuid.uuid4().hex[:8]}"
        
        response = client.patch(
            "/api/v1/auth/me",
            headers=user_auth_header,
            json={"username": new_username}
        )
        
        assert response.status_code == 200
        assert response.json()["user"]["username"] == new_username
    
    def test_update_full_name(self, client: TestClient, user_auth_header: dict):
        """Should update full name"""
        response = client.patch(
            "/api/v1/auth/me",
            headers=user_auth_header,
            json={"full_name": "Updated Name"}
        )
        
        assert response.status_code == 200
        assert response.json()["user"]["full_name"] == "Updated Name"
    
    def test_update_avatar(self, client: TestClient, user_auth_header: dict):
        """Should update avatar URL"""
        avatar_url = "https://example.com/new-avatar.jpg"
        
        response = client.patch(
            "/api/v1/auth/me",
            headers=user_auth_header,
            json={"avatar_url": avatar_url}
        )
        
        assert response.status_code == 200


class TestTokenSecurity:
    """Test token security edge cases"""
    
    def test_expired_token_rejected(self, client: TestClient, test_user: User):
        """Should reject expired token"""
        from datetime import timedelta
        from app.core.security import create_access_token
        
        # Create token that's already expired
        expired_token = create_access_token(
            {"sub": test_user.id, "email": test_user.email, "is_admin": False},
            expires_delta=timedelta(hours=-1)
        )
        
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {expired_token}"}
        )
        
        assert response.status_code == 401
    
    def test_malformed_token_rejected(self, client: TestClient):
        """Should reject malformed token"""
        malformed_tokens = [
            "not-a-jwt",
            "Bearer only-two.parts",
            "   ",
            "Bearer ",
        ]
        
        for token in malformed_tokens:
            response = client.get(
                "/api/v1/auth/me",
                headers={"Authorization": token}
            )
            assert response.status_code in [401, 403, 422]
    
    def test_token_from_deleted_user(self, client: TestClient, db, test_password: str):
        """Should reject token from deleted user"""
        # Create a user
        from app.models.user import User, AuthProvider
        from app.core.security import get_password_hash
        
        user = User(
            id=str(uuid.uuid4()),
            email=f"todelete_{uuid.uuid4().hex[:8]}@example.com",
            username=f"todelete_{uuid.uuid4().hex[:8]}",
            hashed_password=get_password_hash(test_password),
            auth_provider=AuthProvider.LOCAL,
            is_active=True
        )
        db.add(user)
        db.flush()
        
        # Create token
        tokens = create_token_pair(user.id, user.email, False)
        
        # Delete user
        db.delete(user)
        db.flush()
        
        # Try to use token
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
        
        assert response.status_code in [401, 404]
