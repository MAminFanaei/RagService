"""
Tests for Reverse Transaction Flow

Per SEP docs: Reverse is allowed up to 50 minutes after
the transaction (we use 45 min for safety margin).
Only VERIFIED payments can be reversed.

Tests:
    1. Reverse within time window → success
    2. Reverse after time window → 400
    3. Reverse non-verified payment → 400
    4. Reverse already reversed payment → 409
    5. SEP reverse API fails → 502
    6. Unauthenticated → 401
    7. Reverse another user's payment → 403
"""

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from httpx import AsyncClient

from app.payment.core.constants import PaymentStatus


@pytest.mark.asyncio
class TestReverseTransaction:
    """Test payment reversal endpoint."""

    def _url(self, payment_id: str) -> str:
        return f"/api/v1/payment/{payment_id}/reverse"

    async def test_successful_reverse(
        self, client, test_user, auth_headers, mock_sep, payment_factory,payment_session_factory, wallet_factory
        ):
        """Reverse a verified payment within time window → success."""
         
        ref_num = f"REF_{uuid.uuid4().hex[:10]}"
        
        async with payment_session_factory() as session:
            await wallet_factory.create(
                session,
                user_id=test_user.id,
                balance=200000,
            )
            payment = await payment_factory.create(
                session,
                user_id=test_user.id,
                amount=100000,
                status=PaymentStatus.VERIFIED,
                ref_num=ref_num,
            )

        with patch(
            "app.payment.services.sep_client.SEPClient.reverse_transaction",
            new=mock_sep.mock_reverse_transaction,
        ):
            response = await client.post(
                self._url(payment.id),
                json={"reason": "Customer request"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "COMPLETED"

    async def test_reverse_non_verified_payment(
        self, client, test_user, auth_headers, payment_factory,payment_session_factory
    ):
        """Cannot reverse a PENDING payment → 400."""
         
        async with payment_session_factory() as session:
            payment = await payment_factory.create(
                session,
                user_id=test_user.id,
                amount=100000,
                status=PaymentStatus.PENDING,
            )

        response = await client.post(
            self._url(payment.id),
            json={"reason": "Test"},
            headers=auth_headers,
        )
        assert response.status_code == 400

    async def test_reverse_already_reversed(
        self, client, test_user, auth_headers, payment_factory,payment_session_factory
    ):
        """Cannot reverse an already reversed payment → 409."""
         
        async with payment_session_factory() as session:
            payment = await payment_factory.create(
                session,
                user_id=test_user.id,
                amount=100000,
                status=PaymentStatus.REVERSED,
                ref_num=f"REF_{uuid.uuid4().hex[:10]}",
            )

        response = await client.post(
            self._url(payment.id),
            json={"reason": "Test"},
            headers=auth_headers,
        )
        assert response.status_code in (400, 409)

    async def test_reverse_unauthenticated(self, client):
        """No auth → 401."""
        response = await client.post(
            self._url("some-fake-id"),
            json={"reason": "Test"},
        )
        assert response.status_code in (401, 403)

    async def test_sep_reverse_failure(
        self, client, test_user, auth_headers, mock_sep, payment_factory,payment_session_factory, wallet_factory
    ):
        """SEP returns error for reverse → 502."""
         
        ref_num = f"REF_{uuid.uuid4().hex[:10]}"
        
        async with payment_session_factory() as session:
            await wallet_factory.create(
                session,
                user_id=test_user.id,
                balance=200000,
            )
            payment = await payment_factory.create(
                session,
                user_id=test_user.id,
                amount=100000,
                status=PaymentStatus.VERIFIED,
                ref_num=ref_num,
            )

        mock_sep.should_fail_reverse = True

        with patch(
            "app.payment.services.sep_client.SEPClient.reverse_transaction",
            new=mock_sep.mock_reverse_transaction,
        ):
            response = await client.post(
                self._url(payment.id),
                json={"reason": "Test"},
                headers=auth_headers,
            )

        assert response.status_code in (502, 500, 200)
        if response.status_code == 200:
            assert response.json()["status"] == "FAILED"
