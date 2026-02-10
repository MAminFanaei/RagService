# tests/integration/test_admin_endpoints.py
"""
Integration tests for Admin API endpoints.

All tests use httpx.AsyncClient matching the async FastAPI app.
"""

import pytest
from datetime import timedelta

from app.core.security import create_access_token


class TestAdminUserManagement:
    @pytest.mark.asyncio
    async def test_list_users_as_admin(self, client, admin_headers, test_user):
        response = await client.get("/api/v1/admin/users", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert "total" in data
        assert data["total"] >= 1

    @pytest.mark.asyncio
    async def test_list_users_as_non_admin(self, client, auth_headers):
        response = await client.get("/api/v1/admin/users", headers=auth_headers)
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_list_users_unauthenticated(self, client):
        response = await client.get("/api/v1/admin/users")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_list_users_with_search(self, client, admin_headers, test_user):
        response = await client.get(
            "/api/v1/admin/users",
            headers=admin_headers,
            params={"search": test_user.email},
        )
        assert response.status_code == 200
        assert response.json()["total"] >= 1

    @pytest.mark.asyncio
    async def test_list_users_pagination(self, client, admin_headers):
        response = await client.get(
            "/api/v1/admin/users",
            headers=admin_headers,
            params={"skip": 0, "limit": 10},
        )
        assert response.status_code == 200
        data = response.json()
        assert "skip" in data
        assert "limit" in data

    @pytest.mark.asyncio
    async def test_get_user_details(self, client, admin_headers, test_user):
        response = await client.get(
            f"/api/v1/admin/users/{test_user.id}", headers=admin_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == test_user.id
        assert data["email"] == test_user.email
        assert "total_chats" in data
        assert "total_messages" in data

    @pytest.mark.asyncio
    async def test_get_user_details_non_admin(self, client, auth_headers, admin_user):
        response = await client.get(
            f"/api/v1/admin/users/{admin_user.id}", headers=auth_headers
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_get_nonexistent_user(self, client, admin_headers):
        response = await client.get(
            "/api/v1/admin/users/nonexistent-id", headers=admin_headers
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_user_settings(self, client, admin_headers, test_user):
        response = await client.patch(
            f"/api/v1/admin/users/{test_user.id}",
            headers=admin_headers,
            json={"max_messages_per_day": 500, "rate_limit_per_minute": 30},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["max_messages_per_day"] == 500
        assert data["rate_limit_per_minute"] == 30

    @pytest.mark.asyncio
    async def test_promote_to_admin(self, client, admin_headers, test_user):
        response = await client.patch(
            f"/api/v1/admin/users/{test_user.id}",
            headers=admin_headers,
            json={"is_admin": True},
        )
        assert response.status_code == 200
        assert response.json()["is_admin"] is True

    @pytest.mark.asyncio
    async def test_update_user_non_admin(self, client, auth_headers, admin_user):
        response = await client.patch(
            f"/api/v1/admin/users/{admin_user.id}",
            headers=auth_headers,
            json={"max_messages_per_day": 1000},
        )
        assert response.status_code == 403


class TestAdminDisableEnable:
    @pytest.mark.asyncio
    async def test_disable_user(self, client, admin_headers, test_user):
        response = await client.post(
            f"/api/v1/admin/users/{test_user.id}/disable",
            headers=admin_headers,
            json={"reason": "Test disable"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["is_active"] is False
        assert data["user_id"] == test_user.id

    @pytest.mark.asyncio
    async def test_disable_without_reason(self, client, admin_headers, test_user):
        response = await client.post(
            f"/api/v1/admin/users/{test_user.id}/disable",
            headers=admin_headers,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_disable_admin_forbidden(self, client, admin_headers, db):
        from app.models.user import User, AuthProvider
        from app.core.security import get_password_hash

        other_admin = User(
            email="other_admin@example.com",
            username="otheradmin",
            hashed_password=get_password_hash("AdminPass123!"),
            auth_provider=AuthProvider.LOCAL,
            is_active=True,
            is_admin=True,
        )
        db.add(other_admin)
        await db.flush()
        await db.refresh(other_admin)

        response = await client.post(
            f"/api/v1/admin/users/{other_admin.id}/disable",
            headers=admin_headers,
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_disable_self_fails(self, client, admin_headers, admin_user):
        response = await client.post(
            f"/api/v1/admin/users/{admin_user.id}/disable",
            headers=admin_headers,
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_enable_user(self, client, admin_headers, inactive_user):
        response = await client.post(
            f"/api/v1/admin/users/{inactive_user.id}/enable",
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["is_active"] is True

    @pytest.mark.asyncio
    async def test_enable_non_admin(self, client, auth_headers, inactive_user):
        response = await client.post(
            f"/api/v1/admin/users/{inactive_user.id}/enable",
            headers=auth_headers,
        )
        assert response.status_code == 403


class TestAdminDeletion:
    @pytest.mark.asyncio
    async def test_delete_user(self, client, admin_headers, db):
        from app.models.user import User, AuthProvider
        from app.core.security import get_password_hash

        target = User(
            email="todelete@example.com",
            username="todelete",
            hashed_password=get_password_hash("TestPass123!"),
            auth_provider=AuthProvider.LOCAL,
            is_active=True,
            is_admin=False,
        )
        db.add(target)
        await db.flush()
        await db.refresh(target)

        response = await client.request(
            "DELETE",
            f"/api/v1/admin/users/{target.id}",
            headers=admin_headers,
            json={
                "admin_password": "AdminPassword123!",
                "confirm_username": target.username,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == target.id
        assert "chats_deleted" in data
        assert "messages_deleted" in data

    @pytest.mark.asyncio
    async def test_delete_wrong_password(self, client, admin_headers, test_user):
        response = await client.request(
            "DELETE",
            f"/api/v1/admin/users/{test_user.id}",
            headers=admin_headers,
            json={
                "admin_password": "WrongPassword123!",
                "confirm_username": test_user.username,
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_wrong_username(self, client, admin_headers, test_user):
        response = await client.request(
            "DELETE",
            f"/api/v1/admin/users/{test_user.id}",
            headers=admin_headers,
            json={
                "admin_password": "AdminPassword123!",
                "confirm_username": "wrongusername",
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_self_fails(self, client, admin_headers, admin_user):
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

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, client, admin_headers):
        response = await client.request(
            "DELETE",
            "/api/v1/admin/users/nonexistent-id",
            headers=admin_headers,
            json={
                "admin_password": "AdminPassword123!",
                "confirm_username": "nonexistent",
            },
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_non_admin(self, client, auth_headers, admin_user):
        response = await client.request(
            "DELETE",
            f"/api/v1/admin/users/{admin_user.id}",
            headers=auth_headers,
            json={
                "admin_password": "TestPassword123!",
                "confirm_username": admin_user.username,
            },
        )
        assert response.status_code == 403


class TestAdminConversationExport:
    @pytest.mark.asyncio
    async def test_export_user_conversations(self, client, admin_headers, test_user, test_chat):
        response = await client.get(
            f"/api/v1/admin/users/{test_user.id}/conversations",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        conv = data[0]
        assert "chat_id" in conv
        assert "user_id" in conv
        assert "messages" in conv

    @pytest.mark.asyncio
    async def test_export_non_admin(self, client, auth_headers, admin_user):
        response = await client.get(
            f"/api/v1/admin/users/{admin_user.id}/conversations",
            headers=auth_headers,
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_export_nonexistent_user(self, client, admin_headers):
        response = await client.get(
            "/api/v1/admin/users/nonexistent-id/conversations",
            headers=admin_headers,
        )
        assert response.status_code == 404


class TestAdminSystemStats:
    @pytest.mark.asyncio
    async def test_get_system_stats(self, client, admin_headers):
        response = await client.get(
            "/api/v1/admin/stats/user_usage", headers=admin_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert "chats" in data
        assert "messages" in data
        assert "total" in data["users"]
        assert "active" in data["users"]
        assert "total" in data["messages"]
        assert "today" in data["messages"]

    @pytest.mark.asyncio
    async def test_system_stats_non_admin(self, client, auth_headers):
        response = await client.get(
            "/api/v1/admin/stats/user_usage", headers=auth_headers
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_system_stats_unauthenticated(self, client):
        response = await client.get("/api/v1/admin/stats/user_usage")
        assert response.status_code == 403


class TestAdminEdgeCases:
    @pytest.mark.asyncio
    async def test_large_skip_returns_empty(self, client, admin_headers):
        response = await client.get(
            "/api/v1/admin/users",
            headers=admin_headers,
            params={"skip": 999999999},
        )
        assert response.status_code == 200
        assert response.json()["users"] == []

    @pytest.mark.asyncio
    async def test_negative_skip_rejected(self, client, admin_headers):
        response = await client.get(
            "/api/v1/admin/users",
            headers=admin_headers,
            params={"skip": -1},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_zero_limit_rejected(self, client, admin_headers):
        response = await client.get(
            "/api/v1/admin/users",
            headers=admin_headers,
            params={"limit": 0},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_expired_admin_token(self, client, admin_user):
        expired = create_access_token(
            {"sub": admin_user.id, "email": admin_user.email, "is_admin": True},
            expires_delta=timedelta(seconds=-1),
        )
        response = await client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {expired}"},
        )
        assert response.status_code == 401
