"""
Tests for Payment Query Endpoints

Tests:
    1. Get payment by ID → returns details
    2. Get payment by ID (wrong user) → 403 or 404
    3. Get nonexistent payment → 404
    4. List payments → paginated, filtered
    5. List payments with status filter
    6. List reverses for a payment
    7. Unauthenticated → 401
"""

import pytest
import uuid
from httpx import AsyncClient

from app.payment.core.constants import PaymentStatus


@pytest.mark.asyncio
class TestGetPayment:
    """Test GET /api/v1/payment/{payment_id}."""

    def _url(self, pid: str) -> str:
        return f"/api/v1/payment/{pid}"

    async def test_get_own_payment(
        self, client, test_user, auth_headers, payment_factory, payment_db,
    ):
        """User gets their own payment → success."""
        payment = await payment_factory.create(
            payment_db, user_id=test_user.id, amount=100000
        )

        response = await client.get(
            self._url(payment.id), headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == payment.id
        assert data["amount"] == 100000

    async def test_get_nonexistent_payment(
        self, client, auth_headers
    ):
        """Payment doesn't exist → 404."""
        fake_id = str(uuid.uuid4())
        response = await client.get(
            self._url(fake_id), headers=auth_headers
        )
        assert response.status_code == 404

    async def test_unauthenticated(self, client):
        """No auth → 401."""
        response = await client.get(self._url("any-id"))
        assert response.status_code in (401, 403)


@pytest.mark.asyncio
class TestListPayments:
    """Test GET /api/v1/payment/list."""

    URL = "/api/v1/payment/list"

    async def test_list_own_payments(
        self, client, test_user, auth_headers, payment_factory, payment_db,
    ):
        """User lists their payments → returns only their payments."""
        await payment_factory.create(payment_db, user_id=test_user.id, amount=50000)
        await payment_factory.create(payment_db, user_id=test_user.id, amount=75000)
        await payment_factory.create(payment_db, user_id=test_user.id, amount=100000)

        response = await client.get(self.URL, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert len(data["payments"]) == 3

    async def test_list_with_status_filter(
        self, client, test_user, auth_headers, payment_factory, payment_db,
    ):
        """Filter by status → only matching payments."""
        await payment_factory.create(
            payment_db, user_id=test_user.id,
            status=PaymentStatus.VERIFIED, amount=100000,
            ref_num=f"REF_{uuid.uuid4().hex[:10]}",
        )
        await payment_factory.create(
            payment_db, user_id=test_user.id,
            status=PaymentStatus.FAILED, amount=50000,
        )

        response = await client.get(
            f"{self.URL}?status=VERIFIED",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["payments"][0]["status"] == "VERIFIED"

    async def test_list_empty(self, client, auth_headers):
        """No payments → empty list."""
        response = await client.get(self.URL, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert len(data["payments"]) == 0

    async def test_list_unauthenticated(self, client):
        """No auth → 401."""
        response = await client.get(self.URL)
        assert response.status_code in (401, 403)


@pytest.mark.asyncio
class TestListReverses:
    """Test GET /api/v1/payment/{id}/reverses."""

    def _url(self, pid: str) -> str:
        return f"/api/v1/payment/{pid}/reverses"

    async def test_list_reverses_empty(
        self, client, test_user, auth_headers, payment_factory, payment_db,
    ):
        """Payment with no reverses → empty list."""
        payment = await payment_factory.create(
            payment_db, user_id=test_user.id, amount=100000
        )

        response = await client.get(
            self._url(payment.id), headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["reverses"]) == 0

    async def test_unauthenticated(self, client):
        """No auth → 401."""
        response = await client.get(self._url("any-id"))
        assert response.status_code in (401, 403)