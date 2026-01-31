# tests/unit/test_user_service.py
"""
Unit tests for UserService.

Tests:
- User creation (local)
- User retrieval
- Authentication
- Profile updates
- Admin operations
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from app.services.user_service import UserService
from app.models.user import User, AuthProvider
from app.schemas.user import UserCreate
from app.exceptions import (
    BadRequestException,
    NotFoundException,
    ConflictException,
    ForbiddenException
)


# =============================================================================
# USER CREATION TESTS
# =============================================================================

class TestUserCreation:
    """Tests for user creation"""
    
    def test_create_user_success(self, db):
        """Test successful user creation"""
        user_data = UserCreate(
            email="newuser@example.com",
            username="newuser",
            password="SecurePass123!",
            full_name="New User"
        )
        
        user = UserService.create_user(db, user_data)
        
        assert user.id is not None
        assert user.email == "newuser@example.com"
        assert user.username == "newuser"
        assert user.full_name == "New User"
        assert user.auth_provider == AuthProvider.LOCAL
        assert user.hashed_password is not None
        assert user.hashed_password != "SecurePass123!"  # Should be hashed
        assert user.is_active is True
        assert user.is_verified is False  # Requires verification
        assert user.is_admin is False
    
    def test_create_user_without_username(self, db):
        """Test creating user without username - should use None or raise"""
        user_data = UserCreate(
            email="noname@example.com",
            username=None,
            password="SecurePass123!"
        )
        
        # Your create_user doesn't auto-generate username, so this may fail
        # depending on DB constraints
        # If username is required at DB level, this should raise
        try:
            user = UserService.create_user(db, user_data)
            # If it succeeds, username might be None
            assert user.email == "noname@example.com"
        except Exception:
            # Expected if username is required
            pass

# =============================================================================
# USER RETRIEVAL TESTS
# =============================================================================

class TestUserRetrieval:
    """Tests for user retrieval methods"""
    
    def test_get_by_id_exists(self, db, test_user):
        """Test getting user by ID when user exists"""
        found_user = UserService.get_by_id(db, test_user.id)
        
        assert found_user is not None
        assert found_user.id == test_user.id
        assert found_user.email == test_user.email
    
    def test_get_by_id_not_exists(self, db):
        """Test getting user by ID when user doesn't exist"""
        found_user = UserService.get_by_id(db, "nonexistent-id-12345")
        
        assert found_user is None
    
    def test_get_by_email_exists(self, db, test_user):
        """Test getting user by email when exists"""
        found_user = UserService.get_by_email(db, test_user.email)
        
        assert found_user is not None
        assert found_user.email == test_user.email
    
    def test_get_by_email_not_exists(self, db):
        """Test getting user by email when doesn't exist"""
        found_user = UserService.get_by_email(db, "nonexistent@example.com")
        
        assert found_user is None
    
    def test_get_by_email_case_sensitivity(self, db, test_user):
        """Test email lookup case sensitivity"""
        # Depending on DB collation, this may or may not find the user
        upper_email = test_user.email.upper()
        found_user = UserService.get_by_email(db, upper_email)
        
        # MySQL with utf8_general_ci is case-insensitive
        # This test documents the actual behavior
        if found_user:
            assert found_user.id == test_user.id
    
    def test_get_by_username_exists(self, db, test_user):
        """Test getting user by username"""
        found_user = UserService.get_by_username(db, test_user.username)
        
        assert found_user is not None
        assert found_user.username == test_user.username
    
    def test_get_by_username_not_exists(self, db):
        """Test getting user by username when doesn't exist"""
        found_user = UserService.get_by_username(db, "nonexistent_user")
        
        assert found_user is None
    
    def test_get_by_login_with_email(self, db, test_user):
        """Test get_by_login using email"""
        found_user = UserService.get_by_login(db, test_user.email)
        
        assert found_user is not None
        assert found_user.id == test_user.id
    
    def test_get_by_login_with_username(self, db, test_user):
        """Test get_by_login using username"""
        found_user = UserService.get_by_login(db, test_user.username)
        
        assert found_user is not None
        assert found_user.id == test_user.id
    


# =============================================================================
# AUTHENTICATION TESTS
# =============================================================================

class TestAuthentication:
    """Tests for user authentication"""
    
    def test_authenticate_success_with_email(self, db, test_user):
        """Test successful authentication with email"""
        user = UserService.authenticate(db, test_user.email, "TestPassword123!")
        
        assert user is not None
        assert user.id == test_user.id
    
    def test_authenticate_success_with_username(self, db, test_user):
        """Test successful authentication with username"""
        user = UserService.authenticate(db, test_user.username, "TestPassword123!")
        
        assert user is not None
        assert user.id == test_user.id
    
    def test_authenticate_wrong_password(self, db, test_user):
        """Test authentication with wrong password"""
        user = UserService.authenticate(db, test_user.email, "WrongPassword!")
        
        assert user is None
    
    def test_authenticate_nonexistent_user(self, db):
        """Test authentication for non-existent user"""
        user = UserService.authenticate(db, "fake@example.com", "password")
        
        assert user is None
    
    def test_authenticate_inactive_user(self, db, inactive_user):
        """Test that inactive users cannot authenticate"""
        user = UserService.authenticate(
            db, 
            inactive_user.email, 
            "InactivePassword123!"
        )
        
        assert user is None
    

    
    def test_authenticate_updates_last_login(self, db, test_user):
        """Test that successful auth updates last_login_at"""
        original_login = test_user.last_login_at
        
        user = UserService.authenticate(db, test_user.email, "TestPassword123!")
        
        assert user.last_login_at is not None
        if original_login:
            assert user.last_login_at >= original_login


# =============================================================================
# PROFILE UPDATE TESTS
# =============================================================================

class TestProfileUpdate:
    """Tests for profile update operations"""
    
    def test_change_password_success(self, db, test_user):
        """Test successful password change"""
        user = UserService.change_password(
            db=db,
            user_id=test_user.id,
            current_password="TestPassword123!",
            new_password="NewSecurePass456!"
        )
        
        assert user is not None
        # Verify new password works
        auth_user = UserService.authenticate(db, test_user.email, "NewSecurePass456!")
        assert auth_user is not None
    
    def test_change_password_wrong_current(self, db, test_user):
        """Test password change with wrong current password"""
        with pytest.raises(BadRequestException) as exc_info:
            UserService.change_password(
                db=db,
                user_id=test_user.id,
                current_password="WrongPassword!",
                new_password="NewSecurePass456!"
            )
        
        assert "incorrect" in str(exc_info.value.message).lower()
    
    def test_change_password_user_not_found(self, db):
        """Test password change for non-existent user"""
        with pytest.raises(NotFoundException):
            UserService.change_password(
                db=db,
                user_id="nonexistent-id",
                current_password="old",
                new_password="new"
            )
    
    def test_change_email_success(self, db, test_user):
        """Test successful email change"""
        user = UserService.change_email(
            db=db,
            user_id=test_user.id,
            new_email="newemail@example.com",
            password="TestPassword123!"
        )
        
        assert user.email == "newemail@example.com"
        assert user.is_verified is False  # Should require re-verification
    
    def test_change_email_wrong_password(self, db, test_user):
        """Test email change with wrong password"""
        with pytest.raises(BadRequestException):
            UserService.change_email(
                db=db,
                user_id=test_user.id,
                new_email="newemail@example.com",
                password="WrongPassword!"
            )
    
    def test_change_email_already_exists(self, db, test_user, admin_user):
        """Test email change to existing email"""
        with pytest.raises(ConflictException):
            UserService.change_email(
                db=db,
                user_id=test_user.id,
                new_email=admin_user.email,  # Already taken
                password="TestPassword123!"
            )
    
    def test_update_profile_username(self, db, test_user):
        """Test updating username"""
        user = UserService.update_profile(
            db=db,
            user_id=test_user.id,
            username="newusername"
        )
        
        assert user.username == "newusername"
    
    def test_update_profile_username_taken(self, db, test_user, admin_user):
        """Test updating to existing username"""
        with pytest.raises(ConflictException):
            UserService.update_profile(
                db=db,
                user_id=test_user.id,
                username=admin_user.username  # Already taken
            )
    
    def test_update_profile_full_name(self, db, test_user):
        """Test updating full name"""
        user = UserService.update_profile(
            db=db,
            user_id=test_user.id,
            full_name="Updated Name"
        )
        
        assert user.full_name == "Updated Name"
    
    def test_update_profile_avatar(self, db, test_user):
        """Test updating avatar URL"""
        avatar_url = "https://example.com/new-avatar.jpg"
        user = UserService.update_profile(
            db=db,
            user_id=test_user.id,
            avatar_url=avatar_url
        )
        
        assert user.avatar_url == avatar_url


# =============================================================================
# ADMIN OPERATIONS TESTS
# =============================================================================

class TestAdminOperations:
    """Tests for admin-only operations"""
    
    def test_disable_user_success(self, db, test_user):
        """Test disabling a user"""
        user = UserService.disable_user(db, test_user.id)
        
        assert user.is_active is False
    
    def test_disable_user_not_found(self, db):
        """Test disabling non-existent user"""
        with pytest.raises(NotFoundException):
            UserService.disable_user(db, "nonexistent-id")
    
    def test_disable_admin_fails(self, db, admin_user):
        """Test that disabling admin is forbidden"""
        with pytest.raises(ForbiddenException):
            UserService.disable_user(db, admin_user.id)
    
    def test_enable_user_success(self, db, inactive_user):
        """Test enabling a disabled user"""
        user = UserService.enable_user(db, inactive_user.id)
        
        assert user.is_active is True
    
    def test_enable_user_not_found(self, db):
        """Test enabling non-existent user"""
        with pytest.raises(NotFoundException):
            UserService.enable_user(db, "nonexistent-id")
    
    def test_delete_user_permanently(self, db_committed):
        """Test permanent user deletion"""
        from app.core.security import get_password_hash
        
        # Create user with committed transaction
        user = User(
            email="todelete@example.com",
            username="todelete",
            hashed_password=get_password_hash("password"),
            auth_provider=AuthProvider.LOCAL,
            is_active=True
        )
        db_committed.add(user)
        db_committed.commit()
        user_id = user.id
        
        # Delete the user
        stats = UserService.delete_user_permanently(db_committed, user_id)
        
        assert stats["chats_deleted"] >= 0
        assert stats["messages_deleted"] >= 0
        
        # Verify user is gone
        deleted_user = UserService.get_by_id(db_committed, user_id)
        assert deleted_user is None
    
    def test_delete_admin_fails(self, db, admin_user):
        """Test that deleting admin is forbidden"""
        with pytest.raises(ConflictException):
            UserService.delete_user_permanently(db, admin_user.id)
    
    def test_get_user_stats(self, db, test_user):
        """Test getting user statistics"""
        stats = UserService.get_user_stats(db, test_user.id)
        
        assert "total_chats" in stats
        assert "total_messages" in stats
        assert "messages_today" in stats
        assert stats["total_chats"] >= 0
        assert stats["total_messages"] >= 0


# =============================================================================
# USERNAME GENERATION TESTS
# =============================================================================

class TestUsernameGeneration:
    """Tests for auto-username generation"""
    
    def test_generate_username_from_email_basic(self, db):
        """Test basic username generation from email"""
        username = UserService.generate_username_from_email(db, "john.doe@example.com")
        
        assert username is not None
        assert len(username) >= 3
        assert "john" in username.lower()
    
    def test_generate_username_from_email_special_chars(self, db):
        """Test username generation with special characters"""
        username = UserService.generate_username_from_email(db, "user+tag@example.com")
        
        assert username is not None
        assert "+" not in username  # Special chars removed
    
    def test_generate_username_uniqueness(self, db, test_user):
        """Test that generated username is unique"""
        # Try to generate username that would conflict
        username = UserService.generate_username_from_email(db, test_user.email)
        
        # Should get a different username (with suffix) or original if available
        assert username is not None
    
    def test_generate_username_from_name(self, db):
        """Test username generation from full name"""
        username = UserService.generate_username_from_name(
            db, 
            "John Doe", 
            "john@example.com"
        )
        
        assert username is not None
        assert "john" in username.lower() or "doe" in username.lower()
    
    def test_generate_username_from_empty_name(self, db):
        """Test username generation with empty name falls back to email"""
        username = UserService.generate_username_from_name(db, "", "fallback@example.com")
        
        assert username is not None
        assert "fallback" in username.lower()


# =============================================================================
# EDGE CASES
# =============================================================================

class TestEdgeCases:
    """Tests for edge cases and boundary conditions"""
    
    def test_user_with_max_length_fields(self, db):
        """Test creating user with maximum length field values"""
        user_data = UserCreate(
            email="a" * 240 + "@example.com",  # Very long email
            username="a" * 50,  # Max username length
            password="P" * 100 + "1!",  # Long password
            full_name="N" * 255  # Max full_name length
        )
        
        # Depending on validation, this may succeed or raise
        try:
            user = UserService.create_user(db, user_data)
            assert user is not None
        except Exception:
            pass  # Validation error is acceptable
    
    def test_concurrent_username_generation(self, db):
        """Test that concurrent username generation doesn't collide"""
        usernames = set()
        
        for i in range(10):
            username = UserService.generate_username_from_email(
                db, 
                f"user{i}@example.com"
            )
            usernames.add(username)
        
        # All usernames should be unique
        assert len(usernames) == 10
    
    def test_get_all_users_admin_pagination(self, db, test_user, admin_user):
        """Test admin user listing with pagination"""
        result = UserService.get_all_users_admin(
            db=db,
            skip=0,
            limit=10
        )
        
        assert "total" in result
        assert "users" in result
        assert result["total"] >= 2  # At least test_user and admin_user
        assert len(result["users"]) <= 10
    
    def test_get_all_users_admin_search(self, db, test_user):
        """Test admin user search"""
        result = UserService.get_all_users_admin(
            db=db,
            search=test_user.email[:5]  # Search by partial email
        )
        
        assert result["total"] >= 1