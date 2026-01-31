# tests/security/test_authorization.py
"""
Security Tests for Authorization

Tests for authorization vulnerabilities including IDOR, privilege escalation.
"""

import pytest
from fastapi.testclient import TestClient
import uuid

from app.models.user import User
from app.models.chat import ChatSession
from app.core.security import create_token_pair


class TestIDOR:
    """Test for Insecure Direct Object Reference vulnerabilities"""
    
    def test_idor_access_other_user_chat(self, client: TestClient, other_user_chat: tuple, user_auth_header: dict):
        """User should not access other user's chat by ID"""
        other_user, other_chat = other_user_chat
        
        response = client.get(
            f"/api/v1/chats/{other_chat.id}",
            headers=user_auth_header
        )
        
        # Should return 404 (not 403) to not confirm existence
        assert response.status_code == 404
    
    def test_idor_modify_other_user_chat(self, client: TestClient, other_user_chat: tuple, user_auth_header: dict):
        """User should not modify other user's chat"""
        other_user, other_chat = other_user_chat
        
        response = client.patch(
            f"/api/v1/chats/{other_chat.id}",
            headers=user_auth_header,
            json={"title": "Hacked!"}
        )
        
        assert response.status_code == 404
    
    def test_idor_delete_other_user_chat(self, client: TestClient, other_user_chat: tuple, user_auth_header: dict):
        """User should not delete other user's chat"""
        other_user, other_chat = other_user_chat
        
        response = client.delete(
            f"/api/v1/chats/{other_chat.id}",
            headers=user_auth_header
        )
        
        assert response.status_code == 404
    
    def test_idor_send_message_to_other_chat(self, client: TestClient, other_user_chat: tuple, user_auth_header: dict):
        """User should not send message to other user's chat"""
        other_user, other_chat = other_user_chat
        
        response = client.post(
            f"/api/v1/chats/{other_chat.id}/messages",
            headers=user_auth_header,
            json={"content": "Injected message"}
        )
        
        assert response.status_code in [400, 404]
    
    def test_idor_id_enumeration_protection(self, client: TestClient, user_auth_header: dict):
        """
        Test that ID enumeration doesn't reveal information.
        Response should be same whether ID exists or not.
        """
        # Non-existent UUID
        fake_uuid = str(uuid.uuid4())
        
        response = client.get(
            f"/api/v1/chats/{fake_uuid}",
            headers=user_auth_header
        )
        
        assert response.status_code == 404
        # Response should not reveal if chat exists but belongs to another user
    
    def test_idor_uuid_vs_integer_id(self, client: TestClient, user_auth_header: dict):
        """Test that system handles various ID formats safely"""
        invalid_ids = [
            "1",  # Sequential integer
            "0",
            "-1",
            "abc",
            "../../../etc/passwd",
            "'; DROP TABLE chats; --",
            "<script>alert('xss')</script>",
        ]
        
        for invalid_id in invalid_ids:
            response = client.get(
                f"/api/v1/chats/{invalid_id}",
                headers=user_auth_header
            )
            # Should handle gracefully
            assert response.status_code in [404, 422, 400]


class TestPrivilegeEscalation:
    """Test for privilege escalation vulnerabilities"""
    
    def test_user_cannot_access_admin_endpoints(self, client: TestClient, user_auth_header: dict):
        """Regular user should not access admin endpoints"""
        admin_endpoints = [
            ("GET", "/api/v1/admin/users"),
            ("GET", "/api/v1/admin/stats/user_usage"),
            ("GET", f"/api/v1/admin/users/{uuid.uuid4()}"),
            ("PATCH", f"/api/v1/admin/users/{uuid.uuid4()}"),
            ("DELETE", f"/api/v1/admin/users/{uuid.uuid4()}"),
            ("POST", f"/api/v1/admin/users/{uuid.uuid4()}/disable"),
            ("POST", f"/api/v1/admin/users/{uuid.uuid4()}/enable"),
        ]
        
        for method, endpoint in admin_endpoints:
            if method == "GET":
                response = client.get(endpoint, headers=user_auth_header)
            elif method == "POST":
                response = client.post(endpoint, headers=user_auth_header, json={})
            elif method == "PATCH":
                response = client.patch(endpoint, headers=user_auth_header, json={})
            elif method == "DELETE":
                response = client.delete(endpoint, headers=user_auth_header, json={})
            
            assert response.status_code == 403, f"Endpoint {method} {endpoint} should reject non-admin"
    
    def test_forged_admin_claim_rejected(self, client: TestClient, test_user: User, db):
        """
        Test that forged is_admin claim in token is not trusted.
        System should verify admin status from database.
        """
        from app.core.security import create_access_token
        
        # Ensure user is not admin in DB
        assert test_user.is_admin is False
        
        # Create token with forged admin claim
        forged_token = create_access_token({
            "sub": test_user.id,
            "email": test_user.email,
            "is_admin": True  # Forged!
        })
        
        # Try to access admin endpoint
        response = client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {forged_token}"}
        )
        
        # CRITICAL: This should be 403
        # If your system trusts the token claim without DB verification,
        # this is a serious vulnerability!
        
        # Document your expected behavior:
        # Option 1: Verify from DB (SECURE) - response should be 403
        # Option 2: Trust token (VULNERABLE) - response would be 200
        assert response.status_code == 403, "System should verify admin from DB, not token"
    
    def test_cannot_make_self_admin_via_profile(self, client: TestClient, user_auth_header: dict, test_user: User, db):
        """User should not be able to make themselves admin via profile update"""
        response = client.patch(
            "/api/v1/auth/me",
            headers=user_auth_header,
            json={
                "is_admin": True,  # Try to escalate
                "full_name": "Hacker"
            }
        )
        
        # Request might succeed for other fields
        if response.status_code == 200:
            # Verify admin status didn't change
            db.refresh(test_user)
            assert test_user.is_admin is False
    
    def test_cannot_escalate_via_hidden_field(self, client: TestClient, user_auth_header: dict, test_user: User, db):
        """Test that hidden/extra fields in request are ignored"""
        response = client.patch(
            "/api/v1/auth/me",
            headers=user_auth_header,
            json={
                "full_name": "Normal User",
                "is_admin": True,
                "is_active": True,
                "is_verified": True,
                "max_messages_per_day": 999999,
                "rate_limit_per_minute": 999999,
            }
        )
        
        if response.status_code == 200:
            db.refresh(test_user)
            assert test_user.is_admin is False
            # Rate limits should not change via profile endpoint
    
    def test_admin_cannot_access_super_admin_functions(self, client: TestClient, admin_auth_header: dict, test_admin: User):
        """Test that there are no super-admin functions accessible"""
        # If you have super-admin concept, test here
        pass


class TestHorizontalPrivilegeEscalation:
    """Test for horizontal privilege escalation (accessing other users' data)"""
    
    def test_access_other_user_profile(self, client: TestClient, user_auth_header: dict, test_admin: User):
        """User should not access another user's profile via /me tricks"""
        # Try various ways to access other user's data
        
        # Direct access shouldn't work (no such endpoint exists typically)
        response = client.get(
            f"/api/v1/users/{test_admin.id}",  # This endpoint shouldn't exist for regular users
            headers=user_auth_header
        )
        assert response.status_code in [403, 404, 405]
    
    def test_modify_other_user_profile(self, client: TestClient, user_auth_header: dict, test_admin: User):
        """User should not modify another user's profile"""
        response = client.patch(
            f"/api/v1/users/{test_admin.id}",  # This shouldn't exist
            headers=user_auth_header,
            json={"full_name": "Hacked Name"}
        )
        assert response.status_code in [403, 404, 405]
    
    def test_view_other_user_chats_list(self, client: TestClient, user_auth_header: dict, test_admin: User):
        """User should not view another user's chat list"""
        # If there's an endpoint that accepts user_id parameter
        response = client.get(
            f"/api/v1/chats?user_id={test_admin.id}",
            headers=user_auth_header
        )
        
        # The endpoint should ignore user_id parameter or return 403
        # Current implementation should only return current user's chats
        if response.status_code == 200:
            data = response.json()
            for chat in data.get("chats", []):
                assert chat["user_id"] != test_admin.id


class TestResourceAccessControl:
    """Test resource-level access control"""
    
    def test_deleted_chat_not_accessible(self, client: TestClient, deleted_chat: ChatSession, user_auth_header: dict):
        """Deleted chats should not be accessible"""
        response = client.get(
            f"/api/v1/chats/{deleted_chat.id}",
            headers=user_auth_header
        )
        
        assert response.status_code == 404
    
    def test_deleted_user_token_rejected(self, client: TestClient, db, test_password: str):
        """Token from deleted user should be rejected"""
        from app.models.user import User, AuthProvider
        from app.core.security import get_password_hash
        
        # Create user
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
    
    def test_disabled_user_token_rejected(self, client: TestClient, inactive_user: User):
        """Token from disabled user should be rejected"""
        tokens = create_token_pair(inactive_user.id, inactive_user.email, False)
        
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
        
        assert response.status_code == 403


class TestAPIAccessControl:
    """Test API-level access control"""
    
    def test_unauthenticated_endpoints(self, client: TestClient):
        """Test which endpoints are accessible without auth"""
        public_endpoints = [
            ("GET", "/"),
            ("GET", "/health"),
            ("POST", "/api/v1/auth/register"),
            ("POST", "/api/v1/auth/login"),
            ("POST", "/api/v1/auth/refresh"),
        ]
        
        protected_endpoints = [
            ("GET", "/api/v1/auth/me"),
            ("POST", "/api/v1/auth/logout"),
            ("GET", "/api/v1/chats"),
            ("POST", "/api/v1/chats"),
            ("GET", "/api/v1/admin/users"),
        ]
        
        for method, endpoint in public_endpoints:
            if method == "GET":
                response = client.get(endpoint)
            else:
                response = client.post(endpoint, json={})
            
            # Public endpoints should not return 401/403 (might return 422 for validation)
            assert response.status_code not in [401, 403], f"{endpoint} should be public"
        
        for method, endpoint in protected_endpoints:
            if method == "GET":
                response = client.get(endpoint)
            else:
                response = client.post(endpoint, json={})
            
            # Protected endpoints should require auth
            assert response.status_code in [401, 403], f"{endpoint} should be protected"
    
    def test_method_not_allowed(self, client: TestClient, user_auth_header: dict, test_chat: ChatSession):
        """Test that unsupported HTTP methods are rejected"""
        response = client.put(
            f"/api/v1/chats/{test_chat.id}",
            headers=user_auth_header,
            json={"title": "Test"}
        )
        
        assert response.status_code in [405, 422]
    
    def test_cors_headers(self, client: TestClient):
        """Test CORS configuration"""
        response = client.options(
            "/api/v1/auth/login",
            headers={"Origin": "http://evil-site.com"}
        )
        
        # Check CORS headers
        # This depends on your CORS configuration
        cors_origin = response.headers.get("access-control-allow-origin", "")
        
        # Document expected behavior
        # If you have restrictive CORS, evil-site.com should not be allowed
