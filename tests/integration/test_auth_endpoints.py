# tests/integration/test_auth_endpoints.py
"""
Integration tests for Authentication endpoints.

All tests use httpx.AsyncClient (async) matching the async FastAPI app.
"""

import pytest
import uuid

from app.models.user import User
from app.core.security import create_token_pair, decode_token


class TestRegistration:
    @pytest.mark.asyncio
    async def test_register_success(self, client):
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": f"new_{uuid.uuid4().hex[:8]}@example.com",
                "username": f"new_{uuid.uuid4().hex[:8]}",
                "password": "SecurePassword123!",
                "full_name": "New User",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_register_duplicate_email(self, client, test_user):
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": test_user.email,
                "username": f"unique_{uuid.uuid4().hex[:8]}",
                "password": "SecurePassword123!",
            },
        )
        assert response.status_code == 400
        assert "already registered" in response.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_register_duplicate_username(self, client, test_user):
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": f"unique_{uuid.uuid4().hex[:8]}@example.com",
                "username": test_user.username,
                "password": "SecurePassword123!",
            },
        )
        assert response.status_code == 400
        assert "username" in response.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_register_weak_password(self, client):
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": f"t_{uuid.uuid4().hex[:8]}@example.com",
                "username": f"t_{uuid.uuid4().hex[:8]}",
                "password": "weak",
            },
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_register_invalid_email(self, client):
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "not-an-email",
                "username": "validuser",
                "password": "SecurePassword123!",
            },
        )
        assert response.status_code == 422


class TestLogin:
    @pytest.mark.asyncio
    async def test_login_with_email(self, client, test_user, test_password):
        response = await client.post(
            "/api/v1/auth/login",
            json={"login": test_user.email, "password": test_password},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data

    @pytest.mark.asyncio
    async def test_login_with_username(self, client, test_user, test_password):
        response = await client.post(
            "/api/v1/auth/login",
            json={"login": test_user.username, "password": test_password},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, client, test_user):
        response = await client.post(
            "/api/v1/auth/login",
            json={"login": test_user.email, "password": "wrongpassword"},
        )
        assert response.status_code == 401
        assert "incorrect" in response.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_login_nonexistent_user(self, client):
        response = await client.post(
            "/api/v1/auth/login",
            json={"login": "nobody@example.com", "password": "any"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_login_inactive_user(self, client, inactive_user, test_password):
        response = await client.post(
            "/api/v1/auth/login",
            json={"login": inactive_user.email, "password": test_password},
        )
        assert response.status_code == 401


class TestTokenRefresh:
    @pytest.mark.asyncio
    async def test_refresh_success(self, client, test_user, test_password):
        login = await client.post(
            "/api/v1/auth/login",
            json={"login": test_user.email, "password": test_password},
        )
        assert login.status_code == 200
        refresh_token = login.json()["refresh_token"]

        refresh = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert refresh.status_code == 200
        new_tokens = refresh.json()
        assert "access_token" in new_tokens
        assert "refresh_token" in new_tokens

    @pytest.mark.asyncio
    async def test_refresh_with_access_token_fails(self, client, test_user, test_password):
        login = await client.post(
            "/api/v1/auth/login",
            json={"login": test_user.email, "password": test_password},
        )
        access_token = login.json()["access_token"]
        refresh = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": access_token},
        )
        assert refresh.status_code == 401

    @pytest.mark.asyncio
    async def test_refresh_invalid_token(self, client):
        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "invalid.token.here"},
        )
        assert response.status_code == 401


class TestLogout:
    @pytest.mark.asyncio
    async def test_logout_success(self, client, auth_headers):
        response = await client.post("/api/v1/auth/logout", headers=auth_headers)
        assert response.status_code == 200
        assert "logged out" in response.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_logout_invalidates_token(self, client, auth_headers):
        await client.post("/api/v1/auth/logout", headers=auth_headers)
        response = await client.get("/api/v1/auth/me", headers=auth_headers)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_logout_without_token(self, client):
        response = await client.post("/api/v1/auth/logout")
        assert response.status_code in [401, 403]


class TestCurrentUser:
    @pytest.mark.asyncio
    async def test_get_me(self, client, test_user, auth_headers):
        response = await client.get("/api/v1/auth/me", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == test_user.email
        assert data["username"] == test_user.username
        assert "remaining_messages_today" in data

    @pytest.mark.asyncio
    async def test_get_me_no_token(self, client):
        response = await client.get("/api/v1/auth/me")
        assert response.status_code in [401, 403]


class TestPasswordChange:
    @pytest.mark.asyncio
    async def test_change_password_success(self, client, auth_headers, test_password):
        response = await client.put(
            "/api/v1/auth/me/password",
            headers=auth_headers,
            json={"current_password": test_password, "new_password": "NewSecure456!"},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_change_password_wrong_current(self, client, auth_headers):
        response = await client.put(
            "/api/v1/auth/me/password",
            headers=auth_headers,
            json={"current_password": "wrong", "new_password": "NewSecure456!"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_change_password_weak_new(self, client, auth_headers, test_password):
        response = await client.put(
            "/api/v1/auth/me/password",
            headers=auth_headers,
            json={"current_password": test_password, "new_password": "weak"},
        )
        assert response.status_code == 422


class TestEmailChange:
    @pytest.mark.asyncio
    async def test_change_email_success(self, client, auth_headers, test_password):
        new_email = f"new_{uuid.uuid4().hex[:8]}@example.com"
        response = await client.put(
            "/api/v1/auth/me/email",
            headers=auth_headers,
            json={"new_email": new_email, "password": test_password},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["new_email"] == new_email
        assert data["is_verified"] is False

    @pytest.mark.asyncio
    async def test_change_email_wrong_password(self, client, auth_headers):
        response = await client.put(
            "/api/v1/auth/me/email",
            headers=auth_headers,
            json={"new_email": "new@example.com", "password": "wrong"},
        )
        assert response.status_code == 400


class TestProfileUpdate:
    @pytest.mark.asyncio
    async def test_update_username(self, client, auth_headers):
        new_name = f"updated_{uuid.uuid4().hex[:8]}"
        response = await client.patch(
            "/api/v1/auth/me",
            headers=auth_headers,
            json={"username": new_name},
        )
        assert response.status_code == 200
        assert response.json()["user"]["username"] == new_name

    @pytest.mark.asyncio
    async def test_update_full_name(self, client, auth_headers):
        response = await client.patch(
            "/api/v1/auth/me",
            headers=auth_headers,
            json={"full_name": "Updated Name"},
        )
        assert response.status_code == 200
        assert response.json()["user"]["full_name"] == "Updated Name"
