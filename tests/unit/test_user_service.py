# tests/unit/test_user_service.py
"""
Unit tests for UserService — all async.

Tests use real async DB sessions from conftest for true integration-unit testing.
"""

import pytest
import uuid
from datetime import datetime, timezone

from app.services.user_service import UserService
from app.models.user import User, AuthProvider
from app.schemas.user import UserCreate
from app.middleware.exceptions import (
    BadRequestException,
    NotFoundException,
    ConflictException,
    ForbiddenException,
)


# =============================================================================
# USER CREATION
# =============================================================================


class TestUserCreation:
    @pytest.mark.asyncio
    async def test_create_user_success(self, db):
        user_data = UserCreate(
            email="newuser@example.com",
            username="newuser",
            password="SecurePass123!",
            full_name="New User",
            phone_number="09123456789",
            otp_proof="dummy-proof-token",
        )
        user = await UserService.create_user(db, user_data)

        assert user.id is not None
        assert user.email == "newuser@example.com"
        assert user.username == "newuser"
        assert user.full_name == "New User"
        assert user.auth_provider == AuthProvider.LOCAL
        assert user.hashed_password is not None
        assert user.hashed_password != "SecurePass123!"
        assert user.is_active is True
        assert user.is_verified is True  # OTP verified before registration → is_verified=True
        assert user.is_admin is False

    @pytest.mark.asyncio
    async def test_create_user_password_is_hashed_with_argon2(self, db):
        user_data = UserCreate(
            email="hash_test@example.com",
            username="hashtest",
            password="SecurePass123!",
            phone_number="09123456788",
            otp_proof="dummy-proof-token",
        )
        user = await UserService.create_user(db, user_data)
        assert user.hashed_password.startswith("$argon2")


# =============================================================================
# USER RETRIEVAL
# =============================================================================


class TestUserRetrieval:
    @pytest.mark.asyncio
    async def test_get_by_id_exists(self, db, test_user):
        found = await UserService.get_by_id(db, test_user.id)
        assert found is not None
        assert found.id == test_user.id

    @pytest.mark.asyncio
    async def test_get_by_id_not_exists(self, db):
        assert await UserService.get_by_id(db, "nonexistent-id") is None

    @pytest.mark.asyncio
    async def test_get_by_email_exists(self, db, test_user):
        found = await UserService.get_by_email(db, test_user.email)
        assert found is not None
        assert found.email == test_user.email

    @pytest.mark.asyncio
    async def test_get_by_email_not_exists(self, db):
        assert await UserService.get_by_email(db, "no@example.com") is None

    @pytest.mark.asyncio
    async def test_get_by_username_exists(self, db, test_user):
        found = await UserService.get_by_username(db, test_user.username)
        assert found is not None
        assert found.username == test_user.username

    @pytest.mark.asyncio
    async def test_get_by_username_not_exists(self, db):
        assert await UserService.get_by_username(db, "nonexistent_user") is None

    @pytest.mark.asyncio
    async def test_get_by_login_email(self, db, test_user):
        found = await UserService.get_by_login(db, test_user.email)
        assert found is not None
        assert found.id == test_user.id

    @pytest.mark.asyncio
    async def test_get_by_login_username(self, db, test_user):
        found = await UserService.get_by_login(db, test_user.username)
        assert found is not None
        assert found.id == test_user.id


# =============================================================================
# AUTHENTICATION (async password verification)
# =============================================================================


class TestAuthentication:
    @pytest.mark.asyncio
    async def test_authenticate_success_email(self, db, test_user, test_password):
        user = await UserService.authenticate(db, test_user.email, test_password)
        assert user is not None
        assert user.id == test_user.id

    @pytest.mark.asyncio
    async def test_authenticate_success_username(self, db, test_user, test_password):
        user = await UserService.authenticate(db, test_user.username, test_password)
        assert user is not None
        assert user.id == test_user.id

    @pytest.mark.asyncio
    async def test_authenticate_wrong_password(self, db, test_user):
        assert await UserService.authenticate(db, test_user.email, "WrongPassword!") is None

    @pytest.mark.asyncio
    async def test_authenticate_nonexistent_user(self, db):
        assert await UserService.authenticate(db, "fake@example.com", "password") is None

    @pytest.mark.asyncio
    async def test_authenticate_inactive_user(self, db, inactive_user, test_password):
        """Inactive users cannot authenticate — must use the CORRECT password."""
        result = await UserService.authenticate(db, inactive_user.email, test_password)
        assert result is None

    @pytest.mark.asyncio
    async def test_authenticate_updates_last_login(self, db, test_user, test_password):
        original = test_user.last_login_at
        user = await UserService.authenticate(db, test_user.email, test_password)
        assert user.last_login_at is not None
        if original:
            assert user.last_login_at >= original


# =============================================================================
# PROFILE UPDATES
# =============================================================================


class TestProfileUpdate:
    @pytest.mark.asyncio
    async def test_change_password_success(self, db, test_user, test_password):
        user = await UserService.change_password(
            db=db, user_id=test_user.id,
            current_password=test_password, new_password="NewSecurePass456!",
        )
        assert user is not None
        auth = await UserService.authenticate(db, test_user.email, "NewSecurePass456!")
        assert auth is not None

    @pytest.mark.asyncio
    async def test_change_password_wrong_current(self, db, test_user):
        with pytest.raises(BadRequestException, match="(?i)incorrect"):
            await UserService.change_password(
                db=db, user_id=test_user.id,
                current_password="WrongPassword!", new_password="New123!",
            )

    @pytest.mark.asyncio
    async def test_change_password_user_not_found(self, db):
        with pytest.raises(NotFoundException):
            await UserService.change_password(
                db=db, user_id="nonexistent", current_password="old", new_password="new",
            )

    @pytest.mark.asyncio
    async def test_change_email_success(self, db, test_user, test_password):
        user = await UserService.change_email(
            db=db, user_id=test_user.id,
            new_email="newemail@example.com", password=test_password,
        )
        assert user.email == "newemail@example.com"
        assert user.is_verified is False

    @pytest.mark.asyncio
    async def test_change_email_wrong_password(self, db, test_user):
        with pytest.raises(BadRequestException):
            await UserService.change_email(
                db=db, user_id=test_user.id,
                new_email="new@example.com", password="WrongPassword!",
            )

    @pytest.mark.asyncio
    async def test_change_email_already_exists(self, db, test_user, admin_user, test_password):
        with pytest.raises(ConflictException):
            await UserService.change_email(
                db=db, user_id=test_user.id,
                new_email=admin_user.email, password=test_password,
            )

    @pytest.mark.asyncio
    async def test_update_profile_username(self, db, test_user):
        user = await UserService.update_profile(db=db, user_id=test_user.id, username="newusername")
        assert user.username == "newusername"

    @pytest.mark.asyncio
    async def test_update_profile_username_taken(self, db, test_user, admin_user):
        with pytest.raises(ConflictException):
            await UserService.update_profile(
                db=db, user_id=test_user.id, username=admin_user.username,
            )

    @pytest.mark.asyncio
    async def test_update_profile_full_name(self, db, test_user):
        user = await UserService.update_profile(db=db, user_id=test_user.id, full_name="Updated Name")
        assert user.full_name == "Updated Name"

    @pytest.mark.asyncio
    async def test_update_profile_avatar(self, db, test_user):
        url = "https://example.com/avatar.jpg"
        user = await UserService.update_profile(db=db, user_id=test_user.id, avatar_url=url)
        assert user.avatar_url == url


# =============================================================================
# ADMIN OPERATIONS
# =============================================================================


class TestAdminOperations:
    @pytest.mark.asyncio
    async def test_disable_user(self, db, test_user):
        user = await UserService.disable_user(db, test_user.id)
        assert user.is_active is False

    @pytest.mark.asyncio
    async def test_disable_user_not_found(self, db):
        with pytest.raises(NotFoundException):
            await UserService.disable_user(db, "nonexistent-id")

    @pytest.mark.asyncio
    async def test_disable_admin_forbidden(self, db, admin_user):
        with pytest.raises(ForbiddenException):
            await UserService.disable_user(db, admin_user.id)

    @pytest.mark.asyncio
    async def test_enable_user(self, db, inactive_user):
        user = await UserService.enable_user(db, inactive_user.id)
        assert user.is_active is True

    @pytest.mark.asyncio
    async def test_enable_user_not_found(self, db):
        with pytest.raises(NotFoundException):
            await UserService.enable_user(db, "nonexistent-id")

    @pytest.mark.asyncio
    async def test_delete_user_permanently(self, db):
        """Test permanent user deletion.
        
        Note: delete_user_permanently calls db.commit() internally.
        The db fixture wraps everything in a transaction that rolls back,
        but the commit inside a nested transaction becomes a flush with
        some drivers. This tests the logic flow correctly.
        """
        from app.core.security import get_password_hash

        user = User(
            email="todelete@example.com",
            username="todelete",
            hashed_password=get_password_hash("password"),
            auth_provider=AuthProvider.LOCAL,
            is_active=True,
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        user_id = user.id

        # Verify user exists before deletion
        assert await UserService.get_by_id(db, user_id) is not None

        stats = await UserService.delete_user_permanently(db, user_id)
        assert stats["chats_deleted"] >= 0
        assert stats["messages_deleted"] >= 0
        assert await UserService.get_by_id(db, user_id) is None

    @pytest.mark.asyncio
    async def test_delete_admin_fails(self, db, admin_user):
        with pytest.raises(ConflictException):
            await UserService.delete_user_permanently(db, admin_user.id)

    @pytest.mark.asyncio
    async def test_get_user_stats(self, db, test_user):
        stats = await UserService.get_user_stats(db, test_user.id)
        assert "total_chats" in stats
        assert "total_messages" in stats
        assert "messages_today" in stats
        assert stats["total_chats"] >= 0


# =============================================================================
# USERNAME GENERATION
# =============================================================================


class TestUsernameGeneration:
    @pytest.mark.asyncio
    async def test_generate_from_email(self, db):
        username = await UserService.generate_username_from_email(db, "john.doe@example.com")
        assert username is not None
        assert len(username) >= 3

    @pytest.mark.asyncio
    async def test_generate_strips_special_chars(self, db):
        username = await UserService.generate_username_from_email(db, "user+tag@example.com")
        assert "+" not in username

    @pytest.mark.asyncio
    async def test_generate_unique_when_conflict(self, db, test_user):
        username = await UserService.generate_username_from_email(db, test_user.email)
        assert username is not None

    @pytest.mark.asyncio
    async def test_generate_from_name(self, db):
        username = await UserService.generate_username_from_name(db, "John Doe", "john@example.com")
        assert username is not None

    @pytest.mark.asyncio
    async def test_generate_from_empty_name_falls_back(self, db):
        username = await UserService.generate_username_from_name(db, "", "fallback@example.com")
        assert username is not None
        assert "fallback" in username.lower()
