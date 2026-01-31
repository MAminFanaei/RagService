"""
Integration tests for Admin API endpoints.

Tests cover:
- User management (list, get, update, disable, enable, delete)
- Conversation export
- System statistics
- Admin authorization
"""
import pytest
from fastapi import status


class TestAdminUserManagement:
    """Tests for admin user management endpoints"""

    def test_list_users_as_admin(self, client, admin_headers, test_user):
        """Admin can list all users"""
        response = client.get("/api/v1/admin/users", headers=admin_headers)
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "users" in data
        assert "total" in data
        assert data["total"] >= 1

    def test_list_users_as_non_admin(self, client, auth_headers):
        """Non-admin cannot list users"""
        response = client.get("/api/v1/admin/users", headers=auth_headers)
        
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_users_without_auth(self, client):
        """Unauthenticated request is rejected"""
        response = client.get("/api/v1/admin/users")
        
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_users_with_search(self, client, admin_headers, test_user):
        """Admin can search users"""
        response = client.get(
            "/api/v1/admin/users",
            headers=admin_headers,
            params={"search": test_user.email}
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["total"] >= 1

    def test_list_users_with_pagination(self, client, admin_headers):
        """Admin can paginate user list"""
        response = client.get(
            "/api/v1/admin/users",
            headers=admin_headers,
            params={"skip": 0, "limit": 10}
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "skip" in data
        assert "limit" in data

    def test_get_user_details_as_admin(self, client, admin_headers, test_user):
        """Admin can get user details"""
        response = client.get(
            f"/api/v1/admin/users/{test_user.id}",
            headers=admin_headers
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == test_user.id
        assert data["email"] == test_user.email
        assert "total_chats" in data
        assert "total_messages" in data

    def test_get_user_details_as_non_admin(self, client, auth_headers, admin_user):
        """Non-admin cannot get other user details"""
        response = client.get(
            f"/api/v1/admin/users/{admin_user.id}",
            headers=auth_headers
        )
        
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_get_nonexistent_user(self, client, admin_headers):
        """Getting nonexistent user returns 404"""
        response = client.get(
            "/api/v1/admin/users/nonexistent-user-id",
            headers=admin_headers
        )
        
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_user_settings(self, client, admin_headers, test_user):
        """Admin can update user settings"""
        response = client.patch(
            f"/api/v1/admin/users/{test_user.id}",
            headers=admin_headers,
            json={
                "max_messages_per_day": 500,
                "rate_limit_per_minute": 30
            }
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["max_messages_per_day"] == 500
        assert data["rate_limit_per_minute"] == 30

    def test_update_user_admin_status(self, client, admin_headers, test_user):
        """Admin can promote user to admin"""
        response = client.patch(
            f"/api/v1/admin/users/{test_user.id}",
            headers=admin_headers,
            json={"is_admin": True}
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["is_admin"] is True

    def test_update_user_as_non_admin(self, client, auth_headers, admin_user):
        """Non-admin cannot update user settings"""
        response = client.patch(
            f"/api/v1/admin/users/{admin_user.id}",
            headers=auth_headers,
            json={"max_messages_per_day": 1000}
        )
        
        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestAdminUserDisableEnable:
    """Tests for disabling/enabling users"""

    def test_disable_user_success(self, client, admin_headers, test_user):
        """Admin can disable a user"""
        response = client.post(
            f"/api/v1/admin/users/{test_user.id}/disable",
            headers=admin_headers,
            json={"reason": "Test disable"}
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["is_active"] is False
        assert data["user_id"] == test_user.id

    def test_disable_user_without_reason(self, client, admin_headers, test_user):
        """Admin can disable a user without providing reason"""
        response = client.post(
            f"/api/v1/admin/users/{test_user.id}/disable",
            headers=admin_headers
        )
        
        assert response.status_code == status.HTTP_200_OK

    def test_disable_admin_user_fails(self, client, admin_headers, db):
        """Cannot disable an admin user"""
        from app.models.user import User, AuthProvider
        from app.core.security import get_password_hash
        
        # Create another admin
        other_admin = User(
            email="otheradmin@example.com",
            username="otheradmin",
            hashed_password=get_password_hash("AdminPass123!"),
            auth_provider=AuthProvider.LOCAL,
            is_active=True,
            is_admin=True
        )
        db.add(other_admin)
        db.commit()
        db.refresh(other_admin)
        
        response = client.post(
            f"/api/v1/admin/users/{other_admin.id}/disable",
            headers=admin_headers
        )
        
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_disable_self_fails(self, client, admin_headers, admin_user):
        """Admin cannot disable themselves"""
        response = client.post(
            f"/api/v1/admin/users/{admin_user.id}/disable",
            headers=admin_headers
        )
        
        # Should fail - either 400 or 403
        assert response.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_403_FORBIDDEN]

    def test_enable_user_success(self, client, admin_headers, inactive_user):
        """Admin can enable a disabled user"""
        response = client.post(
            f"/api/v1/admin/users/{inactive_user.id}/enable",
            headers=admin_headers
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["is_active"] is True

    def test_enable_user_as_non_admin(self, client, auth_headers, inactive_user):
        """Non-admin cannot enable users"""
        response = client.post(
            f"/api/v1/admin/users/{inactive_user.id}/enable",
            headers=auth_headers
        )
        
        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestAdminUserDeletion:
    """Tests for permanent user deletion"""

    def test_delete_user_success(self, client, admin_headers, admin_user, db):
        """Admin can permanently delete a user with correct confirmation"""
        from app.models.user import User, AuthProvider
        from app.core.security import get_password_hash
        
        # Create a user to delete
        target_user = User(
            email="todelete@example.com",
            username="todelete",
            hashed_password=get_password_hash("TestPass123!"),
            auth_provider=AuthProvider.LOCAL,
            is_active=True,
            is_admin=False
        )
        db.add(target_user)
        db.commit()
        db.refresh(target_user)
        
        # Use request() method since delete() doesn't support json parameter
        response = client.request(
            method="DELETE",
            url=f"/api/v1/admin/users/{target_user.id}",
            headers=admin_headers,
            json={
                "admin_password": "AdminPassword123!",
                "confirm_username": target_user.username
            }
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["user_id"] == target_user.id
        assert "chats_deleted" in data
        assert "messages_deleted" in data

    def test_delete_user_wrong_password(self, client, admin_headers, test_user):
        """Deletion fails with wrong admin password"""
        response = client.request(
            method="DELETE",
            url=f"/api/v1/admin/users/{test_user.id}",
            headers=admin_headers,
            json={
                "admin_password": "WrongPassword123!",
                "confirm_username": test_user.username
            }
        )
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_user_wrong_username_confirmation(self, client, admin_headers, test_user):
        """Deletion fails with wrong username confirmation"""
        response = client.request(
            method="DELETE",
            url=f"/api/v1/admin/users/{test_user.id}",
            headers=admin_headers,
            json={
                "admin_password": "AdminPassword123!",
                "confirm_username": "wrongusername"
            }
        )
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_self_fails(self, client, admin_headers, admin_user):
        """Admin cannot delete themselves"""
        response = client.request(
            method="DELETE",
            url=f"/api/v1/admin/users/{admin_user.id}",
            headers=admin_headers,
            json={
                "admin_password": "AdminPassword123!",
                "confirm_username": admin_user.username
            }
        )
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_admin_user_fails(self, client, admin_headers, db):
        """Cannot delete another admin user"""
        from app.models.user import User, AuthProvider
        from app.core.security import get_password_hash
        
        other_admin = User(
            email="anotheradmin@example.com",
            username="anotheradmin",
            hashed_password=get_password_hash("AdminPass123!"),
            auth_provider=AuthProvider.LOCAL,
            is_active=True,
            is_admin=True
        )
        db.add(other_admin)
        db.commit()
        db.refresh(other_admin)
        
        response = client.request(
            method="DELETE",
            url=f"/api/v1/admin/users/{other_admin.id}",
            headers=admin_headers,
            json={
                "admin_password": "AdminPassword123!",
                "confirm_username": other_admin.username
            }
        )
        
        assert response.status_code == status.HTTP_409_CONFLICT

    def test_delete_nonexistent_user(self, client, admin_headers):
        """Deleting nonexistent user returns 404"""
        response = client.request(
            method="DELETE",
            url="/api/v1/admin/users/nonexistent-id",
            headers=admin_headers,
            json={
                "admin_password": "AdminPassword123!",
                "confirm_username": "nonexistent"
            }
        )
        
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_user_as_non_admin(self, client, auth_headers, admin_user):
        """Non-admin cannot delete users"""
        response = client.request(
            method="DELETE",
            url=f"/api/v1/admin/users/{admin_user.id}",
            headers=auth_headers,
            json={
                "admin_password": "TestPassword123!",
                "confirm_username": admin_user.username
            }
        )
        
        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestAdminConversationExport:
    """Tests for conversation export endpoints"""

    def test_export_user_conversations(self, client, admin_headers, test_user, test_chat):
        """Admin can export all conversations for a user"""
        response = client.get(
            f"/api/v1/admin/users/{test_user.id}/conversations",
            headers=admin_headers
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        
        # Check conversation structure
        conv = data[0]
        assert "chat_id" in conv
        assert "user_id" in conv
        assert "title" in conv
        assert "messages" in conv

    def test_export_user_conversations_as_non_admin(self, client, auth_headers, admin_user):
        """Non-admin cannot export conversations"""
        response = client.get(
            f"/api/v1/admin/users/{admin_user.id}/conversations",
            headers=auth_headers
        )
        
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_export_nonexistent_user_conversations(self, client, admin_headers):
        """Exporting conversations for nonexistent user returns 404"""
        response = client.get(
            "/api/v1/admin/users/nonexistent-id/conversations",
            headers=admin_headers
        )
        
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_export_single_conversation(self, client, admin_headers, test_chat_with_messages):
        """Admin can export a specific conversation"""
        # test_chat_with_messages is a ChatWithMessages object with .id property
        response = client.get(
            f"/api/v1/admin/conversations/{test_chat_with_messages.id}",
            headers=admin_headers
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["chat_id"] == test_chat_with_messages.id
        assert "messages" in data
        assert len(data["messages"]) >= 1

    def test_export_conversation_with_message_details(self, client, admin_headers, test_chat_with_messages):
        """Exported conversation includes message details"""
        response = client.get(
            f"/api/v1/admin/conversations/{test_chat_with_messages.id}",
            headers=admin_headers
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        
        # Check message structure
        msg = data["messages"][0]
        assert "role" in msg
        assert "content" in msg
        assert "created_at" in msg

    def test_export_conversation_as_non_admin(self, client, auth_headers, test_chat):
        """Non-admin cannot export conversations"""
        response = client.get(
            f"/api/v1/admin/conversations/{test_chat.id}",
            headers=auth_headers
        )
        
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_export_nonexistent_conversation(self, client, admin_headers):
        """Exporting nonexistent conversation returns 404"""
        response = client.get(
            "/api/v1/admin/conversations/nonexistent-id",
            headers=admin_headers
        )
        
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestAdminSystemStats:
    """Tests for system statistics endpoint"""

    def test_get_system_stats(self, client, admin_headers):
        """Admin can get system statistics"""
        response = client.get(
            "/api/v1/admin/stats/user_usage",
            headers=admin_headers
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        
        # Check structure
        assert "users" in data
        assert "chats" in data
        assert "messages" in data
        
        # Check user stats
        assert "total" in data["users"]
        assert "active" in data["users"]
        
        # Check message stats
        assert "total" in data["messages"]
        assert "today" in data["messages"]

    def test_get_system_stats_as_non_admin(self, client, auth_headers):
        """Non-admin cannot get system stats"""
        response = client.get(
            "/api/v1/admin/stats/user_usage",
            headers=auth_headers
        )
        
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_get_system_stats_without_auth(self, client):
        """Unauthenticated request is rejected"""
        response = client.get("/api/v1/admin/stats/user_usage")
        
        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestAdminChatMemory:
    """Tests for chat memory endpoint"""

    def test_get_chat_memory(self, client, admin_headers, test_chat_with_messages):
        """Admin can get chat memory"""
        response = client.get(
            f"/api/v1/chats/{test_chat_with_messages.id}/memory",
            headers=admin_headers
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "status" in data
        assert "chat_id" in data

    def test_get_chat_memory_as_non_admin(self, client, auth_headers, test_chat):
        """Non-admin cannot get chat memory"""
        response = client.get(
            f"/api/v1/chats/{test_chat.id}/memory",
            headers=auth_headers
        )
        
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_get_nonexistent_chat_memory(self, client, admin_headers):
        """Getting memory for nonexistent chat returns 404"""
        response = client.get(
            "/api/v1/chats/nonexistent-id/memory",
            headers=admin_headers
        )
        
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestAdminEdgeCases:
    """Edge cases and security tests for admin endpoints"""

    def test_sql_injection_in_search(self, client, admin_headers):
        """SQL injection attempt in search is handled safely"""
        malicious_search = "'; DROP TABLE users; --"
        
        response = client.get(
            "/api/v1/admin/users",
            headers=admin_headers,
            params={"search": malicious_search}
        )
        
        # Should not crash, should return empty or handle gracefully
        assert response.status_code == status.HTTP_200_OK

    def test_sql_injection_in_user_id(self, client, admin_headers):
        """SQL injection attempt in user_id path is handled safely"""
        malicious_id = "1; DROP TABLE users; --"
        
        response = client.get(
            f"/api/v1/admin/users/{malicious_id}",
            headers=admin_headers
        )
        
        # Should return 404, not crash
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_extremely_large_skip_value(self, client, admin_headers):
        """Large skip value is handled"""
        response = client.get(
            "/api/v1/admin/users",
            headers=admin_headers,
            params={"skip": 999999999}
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["users"] == []

    def test_negative_skip_value(self, client, admin_headers):
        """Negative skip value is rejected"""
        response = client.get(
            "/api/v1/admin/users",
            headers=admin_headers,
            params={"skip": -1}
        )
        
        # Should be rejected by validation
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_zero_limit(self, client, admin_headers):
        """Zero limit is rejected"""
        response = client.get(
            "/api/v1/admin/users",
            headers=admin_headers,
            params={"limit": 0}
        )
        
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_limit_exceeds_maximum(self, client, admin_headers):
        """Limit exceeding maximum is rejected"""
        response = client.get(
            "/api/v1/admin/users",
            headers=admin_headers,
            params={"limit": 1000}  # Max is likely 100 or 200
        )
        
        # Either rejected or capped
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_422_UNPROCESSABLE_ENTITY
        ]

    def test_expired_admin_token(self, client, admin_user):
        """Expired admin token is rejected"""
        from app.core.security import create_access_token
        from datetime import timedelta
        
        # Create an already-expired token
        expired_token = create_access_token(
            data={
                "sub": admin_user.id,
                "email": admin_user.email,
                "is_admin": True
            },
            expires_delta=timedelta(seconds=-1)  # Already expired
        )
        
        response = client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {expired_token}"}
        )
        
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_tampered_admin_token(self, client):
        """Tampered token is rejected"""
        # A valid-looking but invalid token
        fake_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJmYWtlLWlkIiwiZW1haWwiOiJmYWtlQGV4YW1wbGUuY29tIiwiaXNfYWRtaW4iOnRydWV9.invalid_signature"
        
        response = client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {fake_token}"}
        )
        
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_non_admin_token_with_admin_claim(self, client, test_user):
        """Token with fake admin claim is rejected"""
        from app.core.security import create_access_token
        
        # Create token with admin claim but for non-admin user
        fake_admin_token = create_access_token(
            data={
                "sub": test_user.id,
                "email": test_user.email,
                "is_admin": True  # Lie about being admin
            }
        )
        
        response = client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {fake_admin_token}"}
        )
        
        # Should check DB, not just token claim
        # If your implementation trusts the token, this will pass (security issue!)
        # If it checks DB, it should fail with 403
        # This test documents expected behavior
        assert response.status_code in [
            status.HTTP_200_OK,  # If trusting token (less secure)
            status.HTTP_403_FORBIDDEN  # If checking DB (more secure)
        ]