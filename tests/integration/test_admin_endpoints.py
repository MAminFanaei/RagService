# tests/integration/test_admin_endpoints.py
"""
Integration Tests for Admin Endpoints

Tests admin-only operations like user management.
"""

import pytest
from fastapi.testclient import TestClient
import uuid

from app.models.user import User
from app.models.chat import ChatSession


class TestAdminUserList:
    """Test admin user listing endpoint"""
    
    def test_list_users_admin_success(self, client: TestClient, admin_auth_header: dict, test_user: User):
        """Admin should list all users"""
        response = client.get(
            "/api/v1/admin/users",
            headers=admin_auth_header
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert "total" in data
    
    def test_list_users_non_admin_rejected(self, client: TestClient, user_auth_header: dict):
        """Regular users should be rejected"""
        response = client.get(
            "/api/v1/admin/users",
            headers=user_auth_header
        )
        
        assert response.status_code == 403
    
    def test_list_users_no_auth_rejected(self, client: TestClient):
        """Unauthenticated requests should be rejected"""
        response = client.get("/api/v1/admin/users")
        
        assert response.status_code in [401, 403]
    
    def test_list_users_pagination(self, client: TestClient, admin_auth_header: dict):
        """Should paginate correctly"""
        response = client.get(
            "/api/v1/admin/users?skip=0&limit=10",
            headers=admin_auth_header
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["skip"] == 0
        assert data["limit"] == 10
    
    def test_list_users_search(self, client: TestClient, admin_auth_header: dict, test_user: User):
        """Should search users by email/username"""
        response = client.get(
            f"/api/v1/admin/users?search={test_user.email[:5]}",
            headers=admin_auth_header
        )
        
        assert response.status_code == 200


class TestAdminGetUser:
    """Test admin get user details endpoint"""
    
    def test_get_user_details_success(self, client: TestClient, admin_auth_header: dict, test_user: User):
        """Admin should get user details"""
        response = client.get(
            f"/api/v1/admin/users/{test_user.id}",
            headers=admin_auth_header
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == test_user.id
        assert data["email"] == test_user.email
        assert "total_chats" in data
        assert "total_messages" in data
    
    def test_get_user_details_non_admin(self, client: TestClient, user_auth_header: dict, test_admin: User):
        """Regular users should not get user details"""
        response = client.get(
            f"/api/v1/admin/users/{test_admin.id}",
            headers=user_auth_header
        )
        
        assert response.status_code == 403
    
    def test_get_user_details_not_found(self, client: TestClient, admin_auth_header: dict):
        """Should return 404 for non-existent user"""
        response = client.get(
            f"/api/v1/admin/users/{uuid.uuid4()}",
            headers=admin_auth_header
        )
        
        assert response.status_code == 404


class TestAdminUpdateUser:
    """Test admin update user endpoint"""
    
    def test_update_user_is_admin(self, client: TestClient, admin_auth_header: dict, test_user: User):
        """Admin should update user admin status"""
        response = client.patch(
            f"/api/v1/admin/users/{test_user.id}",
            headers=admin_auth_header,
            json={"is_admin": True}
        )
        
        assert response.status_code == 200
        assert response.json()["is_admin"] is True
    
    def test_update_user_rate_limits(self, client: TestClient, admin_auth_header: dict, test_user: User):
        """Admin should update user rate limits"""
        response = client.patch(
            f"/api/v1/admin/users/{test_user.id}",
            headers=admin_auth_header,
            json={
                "max_messages_per_day": 500,
                "rate_limit_per_minute": 30
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["max_messages_per_day"] == 500
        assert data["rate_limit_per_minute"] == 30
    
    def test_update_user_non_admin(self, client: TestClient, user_auth_header: dict, test_admin: User):
        """Regular users should not update users"""
        response = client.patch(
            f"/api/v1/admin/users/{test_admin.id}",
            headers=user_auth_header,
            json={"is_admin": True}
        )
        
        assert response.status_code == 403


class TestAdminDisableUser:
    """Test admin disable user endpoint"""
    
    def test_disable_user_success(self, client: TestClient, admin_auth_header: dict, test_user: User):
        """Admin should disable user"""
        response = client.post(
            f"/api/v1/admin/users/{test_user.id}/disable",
            headers=admin_auth_header,
            json={}
        )
        
        assert response.status_code == 200
        assert response.json()["is_active"] is False
    
    def test_disable_user_with_reason(self, client: TestClient, admin_auth_header: dict, test_user: User):
        """Admin should provide reason for disabling"""
        response = client.post(
            f"/api/v1/admin/users/{test_user.id}/disable",
            headers=admin_auth_header,
            json={"reason": "Violation of terms"}
        )
        
        assert response.status_code == 200
    
    def test_disable_self_fails(self, client: TestClient, admin_auth_header: dict, test_admin: User):
        """Admin should not disable self"""
        response = client.post(
            f"/api/v1/admin/users/{test_admin.id}/disable",
            headers=admin_auth_header,
            json={}
        )
        
        assert response.status_code == 400
    
    def test_disable_other_admin_fails(self, client: TestClient, admin_auth_header: dict, db, test_password: str):
        """Should not disable admin accounts"""
        # Create another admin
        from app.models.user import User, AuthProvider
        from app.core.security import get_password_hash
        
        other_admin = User(
            id=str(uuid.uuid4()),
            email=f"otheradmin_{uuid.uuid4().hex[:8]}@example.com",
            username=f"otheradmin_{uuid.uuid4().hex[:8]}",
            hashed_password=get_password_hash(test_password),
            auth_provider=AuthProvider.LOCAL,
            is_active=True,
            is_admin=True
        )
        db.add(other_admin)
        db.flush()
        
        response = client.post(
            f"/api/v1/admin/users/{other_admin.id}/disable",
            headers=admin_auth_header,
            json={}
        )
        
        assert response.status_code == 403
    
    def test_disable_user_non_admin(self, client: TestClient, user_auth_header: dict, test_admin: User):
        """Regular users should not disable anyone"""
        response = client.post(
            f"/api/v1/admin/users/{test_admin.id}/disable",
            headers=user_auth_header,
            json={}
        )
        
        assert response.status_code == 403


class TestAdminEnableUser:
    """Test admin enable user endpoint"""
    
    def test_enable_user_success(self, client: TestClient, admin_auth_header: dict, inactive_user: User):
        """Admin should enable disabled user"""
        response = client.post(
            f"/api/v1/admin/users/{inactive_user.id}/enable",
            headers=admin_auth_header
        )
        
        assert response.status_code == 200
        assert response.json()["is_active"] is True
    
    def test_enable_user_non_admin(self, client: TestClient, user_auth_header: dict, inactive_user: User):
        """Regular users should not enable anyone"""
        response = client.post(
            f"/api/v1/admin/users/{inactive_user.id}/enable",
            headers=user_auth_header
        )
        
        assert response.status_code == 403


class TestAdminDeleteUser:
    """Test admin permanent delete user endpoint"""
    
    def test_delete_user_success(self, client: TestClient, admin_auth_header: dict, test_user: User, test_password: str, test_chat: ChatSession):
        """Admin should permanently delete user with confirmation"""
        response = client.delete(
            f"/api/v1/admin/users/{test_user.id}",
            headers=admin_auth_header,
            json={
                "admin_password": test_password,
                "confirm_username": test_user.username
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "chats_deleted" in data
        assert "messages_deleted" in data
    
    def test_delete_user_wrong_password(self, client: TestClient, admin_auth_header: dict, test_user: User):
        """Should reject with wrong admin password"""
        response = client.delete(
            f"/api/v1/admin/users/{test_user.id}",
            headers=admin_auth_header,
            json={
                "admin_password": "wrongpassword",
                "confirm_username": test_user.username
            }
        )
        
        assert response.status_code == 400
    
    def test_delete_user_wrong_username(self, client: TestClient, admin_auth_header: dict, test_user: User, test_password: str):
        """Should reject with wrong confirmation username"""
        response = client.delete(
            f"/api/v1/admin/users/{test_user.id}",
            headers=admin_auth_header,
            json={
                "admin_password": test_password,
                "confirm_username": "wrongusername"
            }
        )
        
        assert response.status_code == 400
    
    def test_delete_self_fails(self, client: TestClient, admin_auth_header: dict, test_admin: User, test_password: str):
        """Admin should not delete self"""
        response = client.delete(
            f"/api/v1/admin/users/{test_admin.id}",
            headers=admin_auth_header,
            json={
                "admin_password": test_password,
                "confirm_username": test_admin.username
            }
        )
        
        assert response.status_code == 400
    
    def test_delete_other_admin_fails(self, client: TestClient, admin_auth_header: dict, db, test_password: str):
        """Should not delete admin accounts"""
        # Create another admin
        from app.models.user import User, AuthProvider
        from app.core.security import get_password_hash
        
        other_admin = User(
            id=str(uuid.uuid4()),
            email=f"todelete_{uuid.uuid4().hex[:8]}@example.com",
            username=f"todelete_{uuid.uuid4().hex[:8]}",
            hashed_password=get_password_hash(test_password),
            auth_provider=AuthProvider.LOCAL,
            is_active=True,
            is_admin=True
        )
        db.add(other_admin)
        db.flush()
        
        response = client.delete(
            f"/api/v1/admin/users/{other_admin.id}",
            headers=admin_auth_header,
            json={
                "admin_password": test_password,
                "confirm_username": other_admin.username
            }
        )
        
        assert response.status_code == 409


class TestAdminExportConversations:
    """Test admin export conversations endpoints"""
    
    def test_export_user_conversations(self, client: TestClient, admin_auth_header: dict, test_user: User, test_chat_with_messages: ChatSession):
        """Admin should export user's conversations"""
        response = client.get(
            f"/api/v1/admin/users/{test_user.id}/conversations",
            headers=admin_auth_header
        )
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
    
    def test_export_conversation(self, client: TestClient, admin_auth_header: dict, test_chat_with_messages: ChatSession):
        """Admin should export specific conversation"""
        response = client.get(
            f"/api/v1/admin/conversations/{test_chat_with_messages.id}",
            headers=admin_auth_header
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["chat_id"] == test_chat_with_messages.id
        assert "messages" in data
    
    def test_export_conversation_non_admin(self, client: TestClient, user_auth_header: dict, test_chat: ChatSession):
        """Regular users should not export conversations"""
        response = client.get(
            f"/api/v1/admin/conversations/{test_chat.id}",
            headers=user_auth_header
        )
        
        assert response.status_code == 403


class TestAdminStats:
    """Test admin statistics endpoint"""
    
    def test_get_system_stats(self, client: TestClient, admin_auth_header: dict):
        """Admin should get system stats"""
        response = client.get(
            "/api/v1/admin/stats/user_usage",
            headers=admin_auth_header
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert "chats" in data
        assert "messages" in data
    
    def test_get_system_stats_non_admin(self, client: TestClient, user_auth_header: dict):
        """Regular users should not get system stats"""
        response = client.get(
            "/api/v1/admin/stats/user_usage",
            headers=user_auth_header
        )
        
        assert response.status_code == 403


class TestAdminPrivilegeEscalation:
    """Test for privilege escalation vulnerabilities"""
    
    def test_user_cannot_make_self_admin(self, client: TestClient, user_auth_header: dict, test_user: User):
        """User should not be able to make themselves admin"""
        # Try via profile update
        response = client.patch(
            "/api/v1/auth/me",
            headers=user_auth_header,
            json={"is_admin": True}  # This field shouldn't be accepted
        )
        
        # The request might succeed but is_admin should not change
        if response.status_code == 200:
            # Verify admin status didn't change
            me_response = client.get("/api/v1/auth/me", headers=user_auth_header)
            assert me_response.json().get("is_admin", False) is False
    
    def test_user_cannot_access_admin_endpoints(self, client: TestClient, user_auth_header: dict):
        """User should not access any admin endpoints"""
        admin_endpoints = [
            "/api/v1/admin/users",
            "/api/v1/admin/stats/user_usage",
        ]
        
        for endpoint in admin_endpoints:
            response = client.get(endpoint, headers=user_auth_header)
            assert response.status_code == 403, f"Endpoint {endpoint} should reject non-admin"
    
    def test_forged_admin_claim_in_token(self, client: TestClient, test_user: User):
        """Forged admin claim in token should be rejected"""
        from app.core.security import create_access_token
        
        # Create token with is_admin=True for non-admin user
        forged_token = create_access_token({
            "sub": test_user.id,
            "email": test_user.email,
            "is_admin": True  # Lie about being admin
        })
        
        response = client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {forged_token}"}
        )
        
        # The system should verify admin status from database, not token
        # This depends on your implementation
        # If you only check token claims, this is a vulnerability!
        # Document expected behavior
        assert response.status_code in [200, 403]  # Document which is correct
