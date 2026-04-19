"""
Integration tests for DELETE /api/v1/auth/me endpoint.

Tests full HTTP request flow with real database.
"""

import pytest
from datetime import datetime, timedelta, timezone

from app.config import settings


class TestDeleteAccountEndpoint:
    """Basic endpoint behavior tests."""

    @pytest.mark.asyncio
    async def test_unauthenticated_request_rejected(self, client):
        """No token → 403"""
        response = await client.request(
            "DELETE",
            "/api/v1/auth/me",
            json={
                "password": "Password123!",
                "confirm_deletion": True,
                "forfeit_balance": False,
            },
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_wrong_password_rejected(self, client, auth_headers, test_password):
        """Wrong password → 400"""
        response = await client.request(
            "DELETE",
            "/api/v1/auth/me",
            headers=auth_headers,
            json={
                "password": "WrongPassword999!",
                "confirm_deletion": True,
                "forfeit_balance": False,
            },
        )
        assert response.status_code == 400
        data = response.json()
        assert "password" in data["message"].lower() or "incorrect" in data["message"].lower()

    @pytest.mark.asyncio
    async def test_missing_confirmation_rejected(self, client, auth_headers, test_password):
        """confirm_deletion=False → 400"""
        response = await client.request(
            "DELETE",
            "/api/v1/auth/me",
            headers=auth_headers,
            json={
                "password": test_password,
                "confirm_deletion": False,
                "forfeit_balance": False,
            },
        )
        assert response.status_code == 400
        data = response.json()
        assert "confirm" in data["message"].lower()

class TestWalletBalanceBlocking:
    """Wallet balance validation tests."""

    @pytest.mark.asyncio
    async def test_wallet_balance_without_forfeit_blocked(
        self, client, test_user, test_password, db
    ):
        """Balance > 0 without forfeit_balance → 402"""
        from app.core.security import create_token_pair
        from app.payment.services.wallet_service import WalletService
        
        # Create wallet and add balance (payment_id defaults to None)
        wallet = await WalletService.get_or_create_wallet(db, test_user.id)
        await db.flush()
        
        # Credit without payment_id (manual credit)
        await WalletService.credit(
            db=db,
            user_id=test_user.id,
            amount=150000,
            description="Test balance"
        )
        await db.commit()
        
        tokens = create_token_pair(test_user.id, test_user.email, test_user.is_admin)
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        
        response = await client.request(
            "DELETE",
            "/api/v1/auth/me",
            headers=headers,
            json={
                "password": test_password,
                "confirm_deletion": True,
                "forfeit_balance": False,
            },
        )
        
        assert response.status_code == 402
        data = response.json()
        assert data["data"]["wallet_balance"] == 150000

    @pytest.mark.asyncio
    async def test_wallet_balance_with_forfeit_allowed(
        self, client, test_user, test_password, db
    ):
        """Balance > 0 WITH forfeit_balance=True → 200"""
        from app.core.security import create_token_pair
        from app.payment.services.wallet_service import WalletService
        
        await WalletService.get_or_create_wallet(db, test_user.id)
        await db.flush()
        
        await WalletService.credit(
            db=db,
            user_id=test_user.id,
            amount=150000,
            description="Test balance"
        )
        await db.commit()
        
        tokens = create_token_pair(test_user.id, test_user.email, test_user.is_admin)
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        
        response = await client.request(
            "DELETE",
            "/api/v1/auth/me",
            headers=headers,
            json={
                "password": test_password,
                "confirm_deletion": True,
                "forfeit_balance": True,
            },
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["details"]["forfeited_balance"] == 150000

class TestPendingPaymentBlocking:
    """Pending payment validation tests."""

    @pytest.mark.asyncio
    async def test_recent_pending_payment_blocks_deletion(
        self, client, test_user, test_password, db
    ):
        """Pending payment <1h old → 409"""
        from app.core.security import create_token_pair
        from app.payment.models.payment import Payment
        from app.payment.core.constants import PaymentStatus
        from app.payment.services.wallet_service import WalletService
        import uuid
        
        # Ensure wallet exists first
        await WalletService.get_or_create_wallet(db, test_user.id)
        await db.flush()
        
        # Create recent pending payment
        payment = Payment(
            id=str(uuid.uuid4()),
            user_id=test_user.id,
            res_num=f"PAY-{uuid.uuid4()}",
            original_amount=100000,
            discount_amount=0,
            amount=100000,
            terminal_id="test-terminal",
            status=PaymentStatus.PENDING,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        db.add(payment)
        await db.commit()
        
        tokens = create_token_pair(test_user.id, test_user.email, test_user.is_admin)
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        
        response = await client.request(
            "DELETE",
            "/api/v1/auth/me",
            headers=headers,
            json={
                "password": test_password,
                "confirm_deletion": True,
                "forfeit_balance": False,
            },
        )
        
        assert response.status_code == 409
        data = response.json()
        assert "payment" in data["message"].lower()

    @pytest.mark.asyncio
    async def test_old_pending_payment_cancelled_and_deletion_succeeds(
        self, client, test_user, test_password, db
    ):
        """Pending payment >1h old → auto-cancelled, deletion proceeds"""
        from app.core.security import create_token_pair
        from app.payment.models.payment import Payment
        from app.payment.core.constants import PaymentStatus
        from app.payment.services.wallet_service import WalletService
        from sqlalchemy import select
        import uuid
        
        # Ensure wallet exists
        await WalletService.get_or_create_wallet(db, test_user.id)
        await db.flush()
        
        # Create old pending payment
        payment_id = str(uuid.uuid4())
        payment = Payment(
            id=payment_id,
            user_id=test_user.id,
            res_num=f"PAY-{uuid.uuid4()}",
            original_amount=100000,
            discount_amount=0,
            amount=100000,
            terminal_id="test-terminal",
            status=PaymentStatus.PENDING,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        db.add(payment)
        await db.commit()
        
        tokens = create_token_pair(test_user.id, test_user.email, test_user.is_admin)
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        
        response = await client.request(
            "DELETE",
            "/api/v1/auth/me",
            headers=headers,
            json={
                "password": test_password,
                "confirm_deletion": True,
                "forfeit_balance": False,
            },
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["details"]["cancelled_payments"]) == 1
        
        # Verify payment cancelled
        result = await db.execute(select(Payment).where(Payment.id == payment_id))
        updated_payment = result.scalar_one()
        assert updated_payment.status == PaymentStatus.FAILED


class TestSuccessfulDeletion:
    """Happy path tests."""

    @pytest.mark.asyncio
    async def test_successful_deletion_anonymizes_user(
        self, client, test_user, test_password, db
    ):
        """Successful deletion → user anonymized"""
        from app.core.security import create_token_pair
        from app.payment.services.wallet_service import WalletService
        
        original_email = test_user.email
        
        # Ensure wallet exists
        await WalletService.get_or_create_wallet(db, test_user.id)
        await db.commit()
        
        tokens = create_token_pair(test_user.id, test_user.email, test_user.is_admin)
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        
        response = await client.request(
            "DELETE",
            "/api/v1/auth/me",
            headers=headers,
            json={
                "password": test_password,
                "confirm_deletion": True,
                "forfeit_balance": False,
            },
        )
        
        assert response.status_code == 200
        
        # Verify anonymization
        from app.services.user_service import UserService
        user = await UserService.get_by_id(db, test_user.id)
        assert user.is_active is False
        assert user.deleted_at is not None
        assert user.email != original_email
        assert "@deleted.local" in user.email

    @pytest.mark.asyncio
    async def test_deleted_user_cannot_login(
        self, client, test_user, test_password, db
    ):
        """After deletion, login fails"""
        from app.payment.services.wallet_service import WalletService
        
        original_username = test_user.username
        
        await WalletService.get_or_create_wallet(db, test_user.id)
        await db.commit()
        
        from app.services.user_service import UserService
        await UserService.soft_delete_account(
            db=db,
            user_id=test_user.id,
            password=test_password,
            confirm_deletion=True,
            forfeit_balance=False,
        )
        await db.commit()
        
        response = await client.post(
            "/api/v1/auth/login",
            json={"login": original_username, "password": test_password},
        )
        
        assert response.status_code in [401, 422]

    @pytest.mark.asyncio
    async def test_token_blacklisted_after_deletion(
        self, client, test_user, test_password, db
    ):
        """Token unusable after deletion"""
        from app.core.security import create_token_pair
        from app.payment.services.wallet_service import WalletService
        
        await WalletService.get_or_create_wallet(db, test_user.id)
        await db.commit()
        
        tokens = create_token_pair(test_user.id, test_user.email, test_user.is_admin)
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        
        response = await client.request(
            "DELETE",
            "/api/v1/auth/me",
            headers=headers,
            json={
                "password": test_password,
                "confirm_deletion": True,
                "forfeit_balance": False,
            },
        )
        
        assert response.status_code == 200
        
        # Try using same token
        response = await client.get("/api/v1/auth/me", headers=headers)
        assert response.status_code in [401, 403, 404]


class TestDataRetention:
    """Data retention tests."""

    @pytest.mark.asyncio
    async def test_payment_records_not_deleted(
        self, client, test_user, test_password, db
    ):
        """Payments remain after deletion"""
        from app.core.security import create_token_pair
        from app.payment.models.payment import Payment
        from app.payment.core.constants import PaymentStatus
        from app.payment.services.wallet_service import WalletService
        from sqlalchemy import select
        import uuid
        
        await WalletService.get_or_create_wallet(db, test_user.id)
        await db.flush()
        
        payment_id = str(uuid.uuid4())
        payment = Payment(
            id=payment_id,
            user_id=test_user.id,
            res_num=f"PAY-{uuid.uuid4()}",
            ref_num=f"REF-{uuid.uuid4()}",
            original_amount=100000,
            discount_amount=0,
            amount=100000,
            terminal_id="test-terminal",
            status=PaymentStatus.VERIFIED,
            verified_at=datetime.now(timezone.utc),
        )
        db.add(payment)
        await db.commit()
        
        tokens = create_token_pair(test_user.id, test_user.email, test_user.is_admin)
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        
        response = await client.request(
            "DELETE",
            "/api/v1/auth/me",
            headers=headers,
            json={
                "password": test_password,
                "confirm_deletion": True,
                "forfeit_balance": False,
            },
        )
        
        assert response.status_code == 200
        
        result = await db.execute(select(Payment).where(Payment.id == payment_id))
        assert result.scalar_one_or_none() is not None

    @pytest.mark.asyncio
    async def test_wallet_records_not_deleted(
        self, client, test_user, test_password, db
    ):
        """Wallets remain after deletion"""
        from app.core.security import create_token_pair
        from app.payment.services.wallet_service import WalletService
        from app.payment.models.wallet import Wallet
        from sqlalchemy import select
        
        wallet = await WalletService.get_or_create_wallet(db, test_user.id)
        wallet_id = wallet.id
        await db.flush()
        
        # Credit with proper arguments
        await WalletService.credit(
            db=db,
            user_id=test_user.id,
            amount=50000,
            description="Test credit"
        )
        await db.commit()
        
        tokens = create_token_pair(test_user.id, test_user.email, test_user.is_admin)
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        
        response = await client.request(
            "DELETE",
            "/api/v1/auth/me",
            headers=headers,
            json={
                "password": test_password,
                "confirm_deletion": True,
                "forfeit_balance": True,
            },
        )
        
        assert response.status_code == 200
        
        result = await db.execute(select(Wallet).where(Wallet.id == wallet_id))
        assert result.scalar_one_or_none() is not None

    @pytest.mark.asyncio
    async def test_chat_records_remain(
        self, client, test_user, test_password, test_chat, db
    ):
        """Chats remain until manual cleanup"""
        from app.core.security import create_token_pair
        from app.payment.services.wallet_service import WalletService
        from app.models.chat import ChatSession
        from sqlalchemy import select
        
        await WalletService.get_or_create_wallet(db, test_user.id)
        await db.commit()
        
        chat_id = test_chat.id
        
        tokens = create_token_pair(test_user.id, test_user.email, test_user.is_admin)
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        
        response = await client.request(
            "DELETE",
            "/api/v1/auth/me",
            headers=headers,
            json={
                "password": test_password,
                "confirm_deletion": True,
                "forfeit_balance": False,
            },
        )
        
        assert response.status_code == 200
        
        result = await db.execute(select(ChatSession).where(ChatSession.id == chat_id))
        assert result.scalar_one_or_none() is not None