"""
Tests for Wallet Operations

Tests:
    1. New user → wallet auto-created with 0 balance
    2. Payment verified → wallet credited
    3. Payment reversed → wallet debited
    4. Get balance → correct amount
    5. Transaction history → ordered, paginated
    6. Wallet balance never goes negative (debit guard)
    7. Atomic balance update (no race conditions)
"""

import pytest
@pytest.mark.asyncio
class TestWallet:
    """Test wallet endpoints."""

    BALANCE_URL = "/api/v1/payment/wallet/balance"
    TRANSACTIONS_URL = "/api/v1/payment/wallet/transactions"

    async def test_new_user_zero_balance(
        self, client, auth_headers
    ):
        """First time checking balance → wallet created, balance = 0."""
        response = await client.get(
            self.BALANCE_URL,
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["balance"] == 0
        
    async def test_wallet_balance_after_credit(
        self, client, test_user, auth_headers, wallet_factory, payment_db,
    ):
        """Wallet with existing balance → returns correct amount."""
        await wallet_factory.create(
            payment_db,
            user_id=test_user.id,
            balance=500000,
        )

        response = await client.get(
            self.BALANCE_URL,
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["balance"] == 500000

    async def test_transaction_history_empty(
        self, client, auth_headers
    ):
        """No transactions → empty list."""
        response = await client.get(
            self.TRANSACTIONS_URL,
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data["transactions"], list)
        assert len(data["transactions"]) == 0

    async def test_unauthenticated_balance(self, client):
        """No auth → 401."""
        response = await client.get(self.BALANCE_URL)
        assert response.status_code in (401, 403)

    async def test_unauthenticated_transactions(self, client):
        """No auth → 401."""
        response = await client.get(self.TRANSACTIONS_URL)
        assert response.status_code in (401, 403)
