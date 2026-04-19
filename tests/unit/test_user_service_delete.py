"""
Unit tests for UserService.soft_delete_account()

Tests validation logic with mocked dependencies.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.user_service import UserService
from app.middleware.exceptions import (
    BadRequestException,
    NotFoundException,
    ForbiddenException,
    ConflictException,
)


class TestSoftDeleteAccountValidation:
    """Password and confirmation validation tests."""

    @pytest.mark.asyncio
    async def test_wrong_password_rejected(self, mock_db, mock_user):
        """Wrong password → BadRequestException"""
        with patch("app.services.user_service.UserService.get_by_id", return_value=mock_user):
            with patch("app.services.user_service.verify_password_async", return_value=False):
                with pytest.raises(BadRequestException, match="Incorrect password"):
                    await UserService.soft_delete_account(
                        db=mock_db,
                        user_id=mock_user.id,
                        password="WrongPassword123!",
                        confirm_deletion=True,
                        forfeit_balance=False,
                    )

    @pytest.mark.asyncio
    async def test_missing_confirmation_rejected(self, mock_db, mock_user):
        """confirm_deletion=False → BadRequestException"""
        with patch("app.services.user_service.UserService.get_by_id", return_value=mock_user):
            with patch("app.services.user_service.verify_password_async", return_value=True):
                with pytest.raises(BadRequestException, match="confirm"):
                    await UserService.soft_delete_account(
                        db=mock_db,
                        user_id=mock_user.id,
                        password="CorrectPassword123!",
                        confirm_deletion=False,  # ← MISSING
                        forfeit_balance=False,
                    )

    @pytest.mark.asyncio
    async def test_user_not_found(self, mock_db):
        """Non-existent user → NotFoundException"""
        with patch("app.services.user_service.UserService.get_by_id", return_value=None):
            with pytest.raises(NotFoundException, match="User not found"):
                await UserService.soft_delete_account(
                    db=mock_db,
                    user_id="nonexistent-id",
                    password="Password123!",
                    confirm_deletion=True,
                    forfeit_balance=False,
                )

    @pytest.mark.asyncio
    async def test_already_deleted_user(self, mock_db, mock_user):
        """User with is_active=False → BadRequestException"""
        mock_user.is_active = False
        
        with patch("app.services.user_service.UserService.get_by_id", return_value=mock_user):
            with pytest.raises(BadRequestException, match="already deleted"):
                await UserService.soft_delete_account(
                    db=mock_db,
                    user_id=mock_user.id,
                    password="Password123!",
                    confirm_deletion=True,
                    forfeit_balance=False,
                )


class TestWalletBalanceValidation:
    """Wallet balance blocking tests."""

    @pytest.mark.asyncio
    async def test_wallet_balance_without_forfeit_blocked(self, mock_db, mock_user):
        """Balance > 0 without forfeit_balance → PaymentRequiredException (402)"""
        mock_wallet = MagicMock()
        mock_wallet.balance = 150000
        
        # Create async mock for get_or_create_wallet
        async def mock_get_wallet(db, user_id):
            return mock_wallet
        
        with patch("app.services.user_service.UserService.get_by_id", return_value=mock_user):
            with patch("app.services.user_service.verify_password_async", return_value=True):
                with patch("app.payment.services.wallet_service.WalletService.get_or_create_wallet", new=mock_get_wallet):
                    from app.middleware.exceptions import PaymentRequiredException
                    
                    with pytest.raises(PaymentRequiredException) as exc_info:
                        await UserService.soft_delete_account(
                            db=mock_db,
                            user_id=mock_user.id,
                            password="Password123!",
                            confirm_deletion=True,
                            forfeit_balance=False,  # ← BLOCKER
                        )
                    
                    assert "remaining balance" in str(exc_info.value.message).lower()
                    assert exc_info.value.data["wallet_balance"] == 150000

    @pytest.mark.asyncio
    async def test_wallet_balance_with_forfeit_allowed(self, mock_db, mock_user):
        """Balance > 0 WITH forfeit_balance=True → allowed to proceed"""
        mock_wallet = MagicMock()
        mock_wallet.balance = 150000
        
        # Create async mock
        async def mock_get_wallet(db, user_id):
            return mock_wallet
        
        # Mock Payment query to return empty list (no pending payments)
        mock_result = AsyncMock()
        mock_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        with patch("app.services.user_service.UserService.get_by_id", return_value=mock_user):
            with patch("app.services.user_service.verify_password_async", return_value=True):
                with patch("app.payment.services.wallet_service.WalletService.get_or_create_wallet", new=mock_get_wallet):
                    result = await UserService.soft_delete_account(
                        db=mock_db,
                        user_id=mock_user.id,
                        password="Password123!",
                        confirm_deletion=True,
                        forfeit_balance=True,  # ← ALLOWS DELETION
                    )
                    
                    assert result["forfeited_balance"] == 150000
                    assert result["user_id"] == mock_user.id


class TestPendingPaymentValidation:
    """Pending payment blocking and auto-cancel tests."""

    @pytest.mark.asyncio
    async def test_recent_pending_payment_blocked(self, mock_db, mock_user):
        """Pending payment <1h old → ConflictException (409)"""
        from app.payment.models.payment import Payment
        from app.payment.core.constants import PaymentStatus
        
        # Mock wallet
        mock_wallet = MagicMock()
        mock_wallet.balance = 0
        
        async def mock_get_wallet(db, user_id):
            return mock_wallet
        
        # Mock recent pending payment
        mock_payment = MagicMock(spec=Payment)
        mock_payment.created_at = datetime.now(timezone.utc) - timedelta(minutes=30)  # 30 min ago
        mock_payment.status = PaymentStatus.PENDING
        
        mock_result = AsyncMock()
        mock_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_payment])))
        
        with patch("app.services.user_service.UserService.get_by_id", return_value=mock_user):
            with patch("app.services.user_service.verify_password_async", return_value=True):
                with patch("app.payment.services.wallet_service.WalletService.get_or_create_wallet", new=mock_get_wallet):
                    mock_db.execute = AsyncMock(return_value=mock_result)
                    
                    with pytest.raises(ConflictException, match="payment.*in progress"):
                        await UserService.soft_delete_account(
                            db=mock_db,
                            user_id=mock_user.id,
                            password="Password123!",
                            confirm_deletion=True,
                            forfeit_balance=False,
                        )

    @pytest.mark.asyncio
    async def test_old_pending_payment_cancelled(self, mock_db, mock_user):
        """Pending payment >1h old → auto-cancelled, deletion proceeds"""
        from app.payment.models.payment import Payment
        from app.payment.core.constants import PaymentStatus
        
        # Mock wallet
        mock_wallet = MagicMock()
        mock_wallet.balance = 0
        
        async def mock_get_wallet(db, user_id):
            return mock_wallet
        
        # Mock old pending payment
        mock_payment = MagicMock(spec=Payment)
        mock_payment.id = "old-payment-123"
        mock_payment.amount = 50000
        mock_payment.created_at = datetime.now(timezone.utc) - timedelta(hours=2)  # 2h ago
        mock_payment.status = PaymentStatus.PENDING
        
        # First execute() call → recent pending (empty)
        # Second execute() call → old pending (has one)
        mock_recent_result = AsyncMock()
        mock_recent_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        
        mock_old_result = AsyncMock()
        mock_old_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_payment])))
        
        execute_calls = [mock_recent_result, mock_old_result]
        mock_db.execute = AsyncMock(side_effect=execute_calls)
        
        with patch("app.services.user_service.UserService.get_by_id", return_value=mock_user):
            with patch("app.services.user_service.verify_password_async", return_value=True):
                with patch("app.payment.services.wallet_service.WalletService.get_or_create_wallet", new=mock_get_wallet):
                    result = await UserService.soft_delete_account(
                        db=mock_db,
                        user_id=mock_user.id,
                        password="Password123!",
                        confirm_deletion=True,
                        forfeit_balance=False,
                    )
                    
                    # Verify payment was cancelled
                    assert len(result["cancelled_payments"]) == 1
                    assert result["cancelled_payments"][0]["payment_id"] == "old-payment-123"
                    assert mock_payment.status == PaymentStatus.FAILED
                    assert mock_payment.failure_reason == "Account deletion - payment cancelled"


class TestAnonymizationLogic:
    """Test user data anonymization."""

    @pytest.mark.asyncio
    async def test_user_anonymized_on_success(self, mock_db, mock_user):
        """Successful deletion anonymizes email/phone/username."""
        mock_wallet = MagicMock()
        mock_wallet.balance = 0
        
        async def mock_get_wallet(db, user_id):
            return mock_wallet
        
        # Mock no pending payments
        mock_result = AsyncMock()
        mock_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        original_email = mock_user.email
        original_phone = mock_user.phone_number
        original_username = mock_user.username
        
        with patch("app.services.user_service.UserService.get_by_id", return_value=mock_user):
            with patch("app.services.user_service.verify_password_async", return_value=True):
                with patch("app.payment.services.wallet_service.WalletService.get_or_create_wallet", new=mock_get_wallet):
                    await UserService.soft_delete_account(
                        db=mock_db,
                        user_id=mock_user.id,
                        password="Password123!",
                        confirm_deletion=True,
                        forfeit_balance=False,
                    )
                    
                    # Verify anonymization
                    assert mock_user.is_active is False
                    assert mock_user.deleted_at is not None
                    assert mock_user.email != original_email
                    assert "deleted_" in mock_user.email
                    assert "@deleted.local" in mock_user.email
                    assert mock_user.phone_number is None
                    assert mock_user.username is None
                    assert mock_user.full_name is None
                    assert mock_user.avatar_url is None