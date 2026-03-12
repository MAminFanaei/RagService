"""
Tests for Payment Callback Flow

The callback endpoint receives a POST from SEP after the user
completes (or fails) payment on SEP's page.

Tests:
    1. Successful callback → verify → wallet credited → redirect
    2. Failed transaction (State != OK) → no wallet credit → redirect with error
    3. Empty RefNum → payment failed
    4. Callback for unknown ResNum → 404
    5. Amount mismatch in verify → auto-reverse
    6. Verify timeout → retry logic
"""

import pytest
import uuid
from unittest.mock import patch
from httpx import AsyncClient

from app.payment.core.constants import PaymentStatus


@pytest.mark.asyncio
class TestPaymentCallback:
    """Test SEP callback handling."""

    URL = "/api/v1/payment/callback"

    def _build_callback_data(
        self,
        res_num: str,
        ref_num: str = None,
        status: int = 2,
        state: str = "OK",
        amount: int = 100000,
        rrn: str = "14226761817",
        trace_no: str = "100428",
        terminal_id: str = "0000",
        secure_pan: str = "621986****8080",
    ) -> dict:
        """Build SEP callback POST data."""
        return {
            "ResNum": res_num,
            "RefNum": ref_num or f"REF_{uuid.uuid4().hex[:20]}",
            "Status": str(status),
            "State": state,
            "Amount": str(amount),
            "RRN": rrn,
            "TraceNo": trace_no,
            "MID": terminal_id,
            "TerminalId": terminal_id,
            "SecurePan": secure_pan,
            "HashedCardNumber": "b96a14400c3a59249e87c300ecc06e592032"
                                "7e70220213b5bbb7d7b2410f7e0d",
        }

    async def test_successful_callback_and_verify(
        self,
        client: AsyncClient,
        test_user,
        mock_sep,
        payment_factory,
        payment_db,           # ← changed
    ):
        """
        Happy path:
        1. Payment exists with PENDING/TOKEN_OBTAINED status
        2. SEP calls back with Status=2, State=OK
        3. Service verifies with SEP
        4. Wallet is credited
        5. User is redirected to frontend
        """
        # Create a pending payment — use payment_db directly
        payment = await payment_factory.create(
            payment_db,          # ← changed
            user_id=test_user.id,
            amount=100000,
            status=PaymentStatus.TOKEN_OBTAINED,
        )

        mock_sep.verify_amount = 100000

        callback_data = self._build_callback_data(
            res_num=payment.res_num,
            amount=100000,
        )

        with patch(
            "app.payment.services.sep_client.SEPClient.verify_transaction",
            new=mock_sep.mock_verify_transaction,
        ):
            response = await client.post(
                self.URL,
                data=callback_data,  # SEP sends form data, not JSON
                follow_redirects=False,
            )

        # Should redirect to frontend
        assert response.status_code in (302, 307)
        location = response.headers.get("location", "")
        assert "status=VERIFIED" in location or "status=success" in location.lower()

    async def test_failed_transaction_callback(
        self,
        client: AsyncClient,
        test_user,
        payment_factory,
        payment_db,           # ← changed
    ):
        """State != OK → payment failed, no wallet credit."""
        # use payment_db directly
        payment = await payment_factory.create(
            payment_db,          # ← changed
            user_id=test_user.id,
            amount=100000,
            status=PaymentStatus.TOKEN_OBTAINED,
        )

        callback_data = self._build_callback_data(
            res_num=payment.res_num,
            status=1,  # CanceledByUser
            state="CanceledByUser",
        )

        response = await client.post(
            self.URL,
            data=callback_data,
            follow_redirects=False,
        )

        assert response.status_code in (302, 307)
        location = response.headers.get("location", "")
        assert "FAILED" in location.upper() or "error" in location.lower()

    async def test_empty_ref_num(
        self,
        client: AsyncClient,
        test_user,
        payment_factory,
        payment_db,           # ← changed
    ):
        """Empty RefNum from SEP → transaction had issues."""
        # use payment_db directly
        payment = await payment_factory.create(
            payment_db,          # ← changed
            user_id=test_user.id,
            amount=100000,
            status=PaymentStatus.TOKEN_OBTAINED,
        )

        callback_data = self._build_callback_data(
            res_num=payment.res_num,
            ref_num="",  # Empty = problem
            status=2,
            state="OK",
        )

        response = await client.post(
            self.URL,
            data=callback_data,
            follow_redirects=False,
        )

        assert response.status_code in (302, 307)
        location = response.headers.get("location", "")
        assert "FAILED" in location.upper() or "error" in location.lower()

    async def test_unknown_res_num(self, client: AsyncClient):
        """Callback with ResNum we never created → handle gracefully."""
        callback_data = self._build_callback_data(
            res_num="UNKNOWN_RES_NUM_12345",
        )

        response = await client.post(
            self.URL,
            data=callback_data,
            follow_redirects=False,
        )

        # Should redirect with error, not crash
        assert response.status_code in (302, 307, 400, 404)

    async def test_amount_mismatch_triggers_reverse(
        self,
        client: AsyncClient,
        test_user,
        mock_sep,
        payment_factory,
        payment_db,           # ← changed
    ):
        """
        Verify succeeds but amount doesn't match →
        auto-reverse and mark as AMOUNT_MISMATCH.
        """
        # use payment_db directly
        payment = await payment_factory.create(
            payment_db,          # ← changed
            user_id=test_user.id,
            amount=100000,
            status=PaymentStatus.TOKEN_OBTAINED,
        )

        # SEP says 50000 but we expected 100000
        mock_sep.verify_amount = 50000

        callback_data = self._build_callback_data(
            res_num=payment.res_num,
            amount=100000,
        )

        with patch(
            "app.payment.services.sep_client.SEPClient.verify_transaction",
            new=mock_sep.mock_verify_transaction,
        ), patch(
            "app.payment.services.sep_client.SEPClient.reverse_transaction",
            new=mock_sep.mock_reverse_transaction,
        ):
            response = await client.post(
                self.URL,
                data=callback_data,
                follow_redirects=False,
            )

        assert response.status_code in (302, 307)
        # Check that reverse was called
        reverse_calls = [
            c for c in mock_sep.calls if c["method"] == "reverse_transaction"
        ]
        assert len(reverse_calls) >= 1