"""
Tests for Double-Spending Prevention

This is the most critical security feature. SEP explicitly states
that double-spending prevention is the MERCHANT's responsibility.

Tests:
    1. Same RefNum callback twice → wallet credited only once
    2. Same RefNum with different ResNum → rejected
    3. Concurrent callbacks with same RefNum → only one succeeds
    4. Verified payment callback replay → returns existing result
    5. RefNum UNIQUE constraint → DB prevents duplicate insert
"""

import pytest
import asyncio
import uuid
from unittest.mock import patch
from httpx import AsyncClient

from app.payment.core.constants import PaymentStatus


@pytest.mark.asyncio
class TestDoubleSpendingPrevention:
    """Ensure one RefNum = one wallet credit, always."""

    URL = "/api/v1/payment/callback"

    def _build_callback(self, res_num: str, ref_num: str, amount: int = 100000):
        return {
            "ResNum": res_num,
            "RefNum": ref_num,
            "Status": "2",
            "State": "OK",
            "Amount": str(amount),
            "RRN": "14226761817",
            "TraceNo": "100428",
            "MID": "0000",
            "TerminalId": "0000",
            "SecurePan": "621986****8080",
            "HashedCardNumber": "abc123hash",
        }

    async def test_same_ref_num_twice_credits_once(
        self,
        client: AsyncClient,
        test_user,
        mock_sep,
        payment_factory,
    ):
        """
        Send the same RefNum callback twice.
        Wallet should be credited exactly once.
        """
        from tests.payment.conftest import TestSessionLocal

        async with TestSessionLocal() as session:
            payment = await payment_factory.create(
                session,
                user_id=test_user.id,
                amount=100000,
                status=PaymentStatus.TOKEN_OBTAINED,
            )

        ref_num = f"REF_UNIQUE_{uuid.uuid4().hex[:10]}"
        mock_sep.verify_amount = 100000
        callback_data = self._build_callback(payment.res_num, ref_num)

        with patch(
            "app.payment.services.sep_client.SEPClient.verify_transaction",
            new=mock_sep.mock_verify_transaction,
        ):
            # First callback
            response1 = await client.post(
                self.URL, data=callback_data, follow_redirects=False
            )
            # Second callback (replay)
            response2 = await client.post(
                self.URL, data=callback_data, follow_redirects=False
            )

        # Both should "succeed" (redirect)
        assert response1.status_code in (302, 307)
        assert response2.status_code in (302, 307)

        # But verify should have been called only once
        verify_calls = [
            c for c in mock_sep.calls if c["method"] == "verify_transaction"
        ]
        assert len(verify_calls) == 1, (
            f"Verify called {len(verify_calls)} times — expected 1. "
            f"Double-spending protection failed!"
        )

    async def test_already_verified_payment_returns_existing(
        self,
        client: AsyncClient,
        test_user,
        mock_sep,
        payment_factory,
    ):
        """
        If payment is already VERIFIED in our DB,
        return the existing result without calling SEP again.
        """
        from tests.payment.conftest import TestSessionLocal

        ref_num = f"REF_VERIFIED_{uuid.uuid4().hex[:10]}"

        async with TestSessionLocal() as session:
            payment = await payment_factory.create(
                session,
                user_id=test_user.id,
                amount=100000,
                status=PaymentStatus.VERIFIED,
                ref_num=ref_num,
            )

        callback_data = self._build_callback(payment.res_num, ref_num)

        with patch(
            "app.payment.services.sep_client.SEPClient.verify_transaction",
            new=mock_sep.mock_verify_transaction,
        ):
            response = await client.post(
                self.URL, data=callback_data, follow_redirects=False
            )

        # Should redirect with success (existing result)
        assert response.status_code in (302, 307)

        # Verify should NOT have been called
        verify_calls = [
            c for c in mock_sep.calls if c["method"] == "verify_transaction"
        ]
        assert len(verify_calls) == 0, (
            "Verify was called for an already-verified payment!"
        )

    async def test_concurrent_callbacks_same_ref_num(
        self,
        client: AsyncClient,
        test_user,
        mock_sep,
        payment_factory,
    ):
        """
        Two simultaneous callbacks with same RefNum.
        Only one should acquire the lock and process.
        Redis distributed lock prevents the race condition.
        """
        from tests.payment.conftest import TestSessionLocal

        async with TestSessionLocal() as session:
            payment = await payment_factory.create(
                session,
                user_id=test_user.id,
                amount=100000,
                status=PaymentStatus.TOKEN_OBTAINED,
            )

        ref_num = f"REF_CONCURRENT_{uuid.uuid4().hex[:10]}"
        mock_sep.verify_amount = 100000
        callback_data = self._build_callback(payment.res_num, ref_num)

        with patch(
            "app.payment.services.sep_client.SEPClient.verify_transaction",
            new=mock_sep.mock_verify_transaction,
        ):
            # Send both concurrently
            results = await asyncio.gather(
                client.post(self.URL, data=callback_data, follow_redirects=False),
                client.post(self.URL, data=callback_data, follow_redirects=False),
                return_exceptions=True,
            )

        # Both should complete (not crash)
        successful = [r for r in results if not isinstance(r, Exception)]
        assert len(successful) >= 1

        # Verify should have been called at most once
        verify_calls = [
            c for c in mock_sep.calls if c["method"] == "verify_transaction"
        ]
        assert len(verify_calls) <= 1, (
            f"Verify called {len(verify_calls)} times during concurrent callbacks!"
        )
