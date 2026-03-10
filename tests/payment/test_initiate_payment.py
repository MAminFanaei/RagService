"""
Tests for Payment Initiation Flow

Tests:
    1. Successful payment initiation → returns token + redirect URL
    2. Amount below minimum → 400
    3. Amount above maximum → 400
    4. Missing amount → 422
    5. Unauthenticated request → 401
    6. SEP token request fails → 502
    7. Payment with valid discount code
    8. Payment with invalid discount code → 400
    9. Duplicate ResNum prevention
"""

import pytest
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient

from app.payment.config import payment_settings


@pytest.mark.asyncio
class TestInitiatePayment:
    """Test payment initiation endpoint."""

    URL = "/api/v1/payment/initiate"

    async def test_successful_initiation(
        self, client: AsyncClient, auth_headers: dict, mock_sep
    ):
        """Happy path: valid request → token + redirect URL."""
        with patch(
            "app.payment.services.sep_client.SEPClient.request_token",
            new=mock_sep.mock_request_token,
        ):
            response = await client.post(
                self.URL,
                json={"amount": 100000, "description": "Test charge"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert "payment_id" in data
        assert "token" in data
        assert "redirect_url" in data
        assert data["amount"] == 100000
        assert "sep.shaparak.ir" in data["redirect_url"]

    async def test_amount_below_minimum(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Amount below MIN_PAYMENT_AMOUNT → 400."""
        response = await client.post(
            self.URL,
            json={"amount": 100},  # Below minimum
            headers=auth_headers,
        )
        assert response.status_code == 400

    async def test_amount_above_maximum(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Amount above MAX_PAYMENT_AMOUNT → 400."""
        response = await client.post(
            self.URL,
            json={"amount": 999_999_999_999},
            headers=auth_headers,
        )
        assert response.status_code == 400

    async def test_missing_amount(
        self, client: AsyncClient, auth_headers: dict
    ):
        """No amount field → 422 validation error."""
        response = await client.post(
            self.URL,
            json={"description": "no amount"},
            headers=auth_headers,
        )
        assert response.status_code == 422

    async def test_unauthenticated(self, client: AsyncClient):
        """No JWT token → 401/403."""
        response = await client.post(
            self.URL,
            json={"amount": 100000},
        )
        assert response.status_code in (401, 403)

    async def test_sep_token_failure(
        self, client: AsyncClient, auth_headers: dict, mock_sep
    ):
        """SEP returns error for token request → 502."""
        mock_sep.should_fail_token = True

        with patch(
            "app.payment.services.sep_client.SEPClient.request_token",
            new=mock_sep.mock_request_token,
        ):
            response = await client.post(
                self.URL,
                json={"amount": 100000},
                headers=auth_headers,
            )

        assert response.status_code == 502

    async def test_initiation_with_valid_discount(
        self,
        client: AsyncClient,
        auth_headers: dict,
        mock_sep,
        discount_factory,
        payment_session_factory
    ):
        """Payment with valid discount code → reduced amount."""
        async with payment_session_factory() as session:
            await discount_factory.create(
                session,
                code="SAVE20",
                discount_type="PERCENTAGE",
                discount_value=20,
                max_discount=50000,
            )

        with patch(
            "app.payment.services.sep_client.SEPClient.request_token",
            new=mock_sep.mock_request_token,
        ):
            response = await client.post(
                self.URL,
                json={
                    "amount": 100000,
                    "discount_code": "SAVE20",
                },
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        # 20% of 100000 = 20000 discount
        assert data["discount_amount"] == 20000
        assert data["amount"] == 80000

    async def test_initiation_with_invalid_discount(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Invalid discount code → 400."""
        response = await client.post(
            self.URL,
            json={
                "amount": 100000,
                "discount_code": "NONEXISTENT",
            },
            headers=auth_headers,
        )
        assert response.status_code in (400, 404)


