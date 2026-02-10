# tests/security/test_auth_security.py
"""
Security tests for authentication, authorization, injection, and rate limiting.

Covers OWASP-relevant attack vectors against the API.
"""

import pytest
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock

from app.core.security import (
    create_access_token,
    create_token_pair,
    decode_token,
)
from app.config import settings


# =============================================================================
# AUTHENTICATION SECURITY
# =============================================================================


class TestTokenSecurity:
    """Token-based attack vectors."""

    @pytest.mark.asyncio
    async def test_expired_token_rejected(self, client, test_user):
        expired = create_access_token(
            {"sub": test_user.id, "email": test_user.email, "is_admin": False},
            expires_delta=timedelta(hours=-1),
        )
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {expired}"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_tampered_token_rejected(self, client):
        fake = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiJmYWtlIiwiZW1haWwiOiJmQGUuY29tIiwiaXNfYWRtaW4iOnRydWV9."
            "invalid_signature"
        )
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {fake}"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_malformed_tokens_rejected(self, client):
        for bad in ["not-a-jwt", "Bearer only-two.parts", "   ", ""]:
            response = await client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {bad}"},
            )
            assert response.status_code in [401, 403, 422]

    @pytest.mark.asyncio
    async def test_refresh_token_cannot_access_endpoints(self, client, test_user):
        """Refresh tokens have type='refresh' and must not work as access tokens."""
        tokens = create_token_pair(
            user_id=test_user.id, email=test_user.email, is_admin=False
        )
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {tokens['refresh_token']}"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_token_from_deleted_user(self, client, db, test_password):
        """Token for a user that was subsequently deleted should fail."""
        from app.models.user import User, AuthProvider
        from app.core.security import get_password_hash

        user = User(
            id=str(uuid.uuid4()),
            email=f"del_{uuid.uuid4().hex[:8]}@example.com",
            username=f"del_{uuid.uuid4().hex[:8]}",
            hashed_password=get_password_hash(test_password),
            auth_provider=AuthProvider.LOCAL,
            is_active=True,
        )
        db.add(user)
        await db.flush()

        tokens = create_token_pair(user.id, user.email, False)
        await db.delete(user)
        await db.flush()

        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert response.status_code in [401, 404]


# =============================================================================
# AUTHORIZATION SECURITY
# =============================================================================


class TestAuthorizationSecurity:
    """Privilege escalation and access control tests."""

    @pytest.mark.asyncio
    async def test_non_admin_cannot_access_admin_endpoints(self, client, auth_headers):
        response = await client.get("/api/v1/admin/users", headers=auth_headers)
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_non_admin_cannot_access_system_stats(self, client, auth_headers):
        response = await client.get("/api/v1/admin/stats/user_usage", headers=auth_headers)
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_unauthenticated_admin_access(self, client):
        response = await client.get("/api/v1/admin/users")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_non_admin_token_with_admin_claim_checked_against_db(
        self, client, test_user
    ):
        """
        Token forged with is_admin=True for a non-admin user.
        The system MUST verify against the DB, not trust the token claim.
        """
        forged = create_access_token(
            {"sub": test_user.id, "email": test_user.email, "is_admin": True}
        )
        response = await client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {forged}"},
        )
        # DB says user is NOT admin → must be 403
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_cannot_disable_self(self, client, admin_headers, admin_user):
        response = await client.post(
            f"/api/v1/admin/users/{admin_user.id}/disable",
            headers=admin_headers,
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_admin_cannot_delete_self(self, client, admin_headers, admin_user):
        response = await client.request(
            "DELETE",
            f"/api/v1/admin/users/{admin_user.id}",
            headers=admin_headers,
            json={
                "admin_password": "AdminPassword123!",
                "confirm_username": admin_user.username,
            },
        )
        assert response.status_code == 400


# =============================================================================
# INJECTION SECURITY
# =============================================================================


class TestInjectionSecurity:
    """SQL injection and XSS attack vectors."""

    @pytest.mark.asyncio
    async def test_sql_injection_in_admin_search(self, client, admin_headers):
        response = await client.get(
            "/api/v1/admin/users",
            headers=admin_headers,
            params={"search": "'; DROP TABLE users; --"},
        )
        assert response.status_code == 200  # Handled safely

    @pytest.mark.asyncio
    async def test_sql_injection_in_user_id_path(self, client, admin_headers):
        response = await client.get(
            "/api/v1/admin/users/1; DROP TABLE users; --",
            headers=admin_headers,
        )
        assert response.status_code == 404  # Not found, not crash

    @pytest.mark.asyncio
    async def test_xss_in_chat_title(self, client, auth_headers):
        response = await client.post(
            "/api/v1/chats",
            headers=auth_headers,
            json={"title": "<script>alert('xss')</script>"},
        )
        assert response.status_code == 201
        # Title is stored as-is; XSS prevention is a frontend concern
        assert "<script>" in response.json()["title"]

    @pytest.mark.asyncio
    async def test_xss_in_registration_username(self, client):
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": f"xss_{uuid.uuid4().hex[:8]}@example.com",
                "username": "<img src=x onerror=alert(1)>",
                "password": "SecurePassword123!",
            },
        )
        # Should be rejected by username validation (alphanumeric only)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_oversized_message_rejected(self, client, auth_headers, test_chat):
        long_content = "x" * 10001
        response = await client.post(
            f"/api/v1/chats/{test_chat.id}/messages",
            headers=auth_headers,
            json={"content": long_content},
        )
        assert response.status_code == 400


# =============================================================================
# IDOR (Insecure Direct Object Reference)
# =============================================================================


class TestIDORSecurity:
    """Verify users cannot access other users' resources."""

    @pytest.mark.asyncio
    async def test_idor_get_other_chat(self, client, other_user_chat, auth_headers):
        response = await client.get(
            f"/api/v1/chats/{other_user_chat.chat.id}",
            headers=auth_headers,
        )
        assert response.status_code == 404  # Not 403 — don't confirm existence

    @pytest.mark.asyncio
    async def test_idor_update_other_chat(self, client, other_user_chat, auth_headers):
        response = await client.patch(
            f"/api/v1/chats/{other_user_chat.chat.id}",
            headers=auth_headers,
            json={"title": "Hacked!"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_idor_delete_other_chat(self, client, other_user_chat, auth_headers):
        response = await client.delete(
            f"/api/v1/chats/{other_user_chat.chat.id}",
            headers=auth_headers,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_idor_send_message_other_chat(self, client, other_user_chat, auth_headers):
        response = await client.post(
            f"/api/v1/chats/{other_user_chat.chat.id}/messages",
            headers=auth_headers,
            json={"content": "Injected message"},
        )
        assert response.status_code in [400, 404]

    @pytest.mark.asyncio
    async def test_non_admin_cannot_view_chat_memory(self, client, test_chat, auth_headers):
        response = await client.get(
            f"/api/v1/chats/{test_chat.id}/memory",
            headers=auth_headers,
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_non_admin_cannot_restore_chat(self, client, deleted_chat, auth_headers):
        response = await client.post(
            f"/api/v1/chats/{deleted_chat.id}/restore",
            headers=auth_headers,
        )
        assert response.status_code == 403
