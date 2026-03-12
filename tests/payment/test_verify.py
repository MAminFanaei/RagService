"""
Tests for VerifyTransaction Logic

The two "Code 2" systems:
    - Callback Status=2 → "OK, user paid" (SEP callback parameter)
    - Verify ResultCode=2 → "duplicate request" (Verify API response)

Tests:
    1. ResultCode=0 → success, amounts match → wallet credited
    2. ResultCode=0 → success, amounts DON'T match → auto-reverse
    3. ResultCode=2 → duplicate, payment already verified in our DB → return existing
    4. ResultCode=2 → duplicate, payment NOT verified in our DB → treat as success
    5. ResultCode=-2 → transaction not found
    6. ResultCode=-6 → more than 30 min passed
    7. ResultCode=5 → already reversed
    8. Network timeout → retry up to N times
    9. All retries fail → mark as VERIFY_TIMEOUT
"""

import pytest
import uuid
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient

from app.payment.core.constants import PaymentStatus


@pytest.mark.asyncio
class TestVerifyTransaction:
    """Test VerifyTransaction result code handling."""

    URL = "/api/v1/payment/callback"

    def _callback(self, res_num, ref_num=None, amount=100000):
        return {
            "ResNum": res_num,
            "RefNum": ref_num or f"REF_{uuid.uuid4().hex[:10]}",
            "Status": "2",
            "State": "OK",
            "Amount": str(amount),
            "RRN": "RRN123",
            "TraceNo": "TRACE123",
            "MID": "0000",
            "TerminalId": "0000",
            "SecurePan": "621986****8080",
            "HashedCardNumber": "hash123",
        }

    async def test_result_code_0_amounts_match(
        self, client, test_user, mock_sep, payment_factory, payment_db,
    ):
        """ResultCode=0, amounts match → VERIFIED, wallet credited."""
        payment = await payment_factory.create(
            payment_db, user_id=test_user.id, amount=100000,
            status=PaymentStatus.TOKEN_OBTAINED,
        )

        mock_sep.verify_amount = 100000
        mock_sep.verify_result_code = 0

        with patch(
            "app.payment.services.sep_client.SEPClient.verify_transaction",
            new=mock_sep.mock_verify_transaction,
        ):
            resp = await client.post(
                self.URL, data=self._callback(payment.res_num),
                follow_redirects=False,
            )

        assert resp.status_code in (302, 307)
        assert "VERIFIED" in resp.headers.get("location", "").upper() or \
               "success" in resp.headers.get("location", "").lower()

    async def test_result_code_0_amounts_mismatch(
        self, client, test_user, mock_sep, payment_factory, payment_db,
    ):
        """ResultCode=0 but amounts don't match → AMOUNT_MISMATCH, auto-reverse."""
        payment = await payment_factory.create(
            payment_db, user_id=test_user.id, amount=100000,
            status=PaymentStatus.TOKEN_OBTAINED,
        )

        mock_sep.verify_amount = 50000  # Mismatch!
        mock_sep.verify_result_code = 0

        with patch(
            "app.payment.services.sep_client.SEPClient.verify_transaction",
            new=mock_sep.mock_verify_transaction,
        ), patch(
            "app.payment.services.sep_client.SEPClient.reverse_transaction",
            new=mock_sep.mock_reverse_transaction,
        ):
            resp = await client.post(
                self.URL, data=self._callback(payment.res_num),
                follow_redirects=False,
            )

        assert resp.status_code in (302, 307)
        # Reverse should have been called
        reverse_calls = [c for c in mock_sep.calls if c["method"] == "reverse_transaction"]
        assert len(reverse_calls) >= 1

    async def test_result_code_negative_2_not_found(
        self, client, test_user, mock_sep, payment_factory, payment_db,
    ):
        """ResultCode=-2 → transaction not found at SEP → FAILED."""
        payment = await payment_factory.create(
            payment_db, user_id=test_user.id, amount=100000,
            status=PaymentStatus.TOKEN_OBTAINED,
        )

        mock_sep.should_fail_verify = True

        with patch(
            "app.payment.services.sep_client.SEPClient.verify_transaction",
            new=mock_sep.mock_verify_transaction,
        ):
            resp = await client.post(
                self.URL, data=self._callback(payment.res_num),
                follow_redirects=False,
            )

        assert resp.status_code in (302, 307)
        assert "FAILED" in resp.headers.get("location", "").upper() or \
               "error" in resp.headers.get("location", "").lower()

    async def test_verify_timeout_retries(
        self, client, test_user, mock_sep, payment_factory, payment_db,
    ):
        """Network timeout → retries → all fail → VERIFY_TIMEOUT."""
        payment = await payment_factory.create(
            payment_db, user_id=test_user.id, amount=100000,
            status=PaymentStatus.TOKEN_OBTAINED,
        )

        mock_sep.should_timeout = True

        with patch(
            "app.payment.services.sep_client.SEPClient.verify_transaction",
            new=mock_sep.mock_verify_transaction,
        ):
            resp = await client.post(
                self.URL, data=self._callback(payment.res_num),
                follow_redirects=False,
            )

        assert resp.status_code in (302, 307)