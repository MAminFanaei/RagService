# tests/payment/test_failure_scenarios.py
"""
Tests for things that WILL go wrong in production.
"""

import pytest
import uuid
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient

from app.payment.core.constants import PaymentStatus


@pytest.mark.asyncio
class TestDatabaseFailures:

    URL = "/api/v1/payment/callback"

    def _callback(self, res_num, ref_num=None, amount=100000):
        return {
            "ResNum": res_num,
            "RefNum": ref_num or f"REF_{uuid.uuid4().hex[:10]}",
            "Status": "2", "State": "OK",
            "Amount": str(amount), "RRN": "123",
            "TraceNo": "456", "MID": "0000",
            "TerminalId": "0000",
            "SecurePan": "6219****8080",
            "HashedCardNumber": "hash",
        }

    async def test_callback_does_not_crash_on_any_input(
        self, client: AsyncClient
    ):
        """Send garbage data — should redirect with error, never 500."""
        garbage_inputs = [
            {},
            {"random": "data"},
            {"ResNum": "", "Status": "abc"},
            {"ResNum": "x" * 10000},
            {"Status": "-1", "State": "OK", "RefNum": "ref"},
        ]
        for data in garbage_inputs:
            resp = await client.post(self.URL, data=data, follow_redirects=False)
            assert resp.status_code in (302, 307, 400, 404), (
                f"Got {resp.status_code} for input {data}"
            )


@pytest.mark.asyncio
class TestAmountEdgeCases:

    URL = "/api/v1/payment/initiate"

    async def test_zero_amount(self, client, auth_headers):
        resp = await client.post(
            self.URL, json={"amount": 0}, headers=auth_headers,
        )
        assert resp.status_code in (400, 422)

    async def test_negative_amount(self, client, auth_headers):
        resp = await client.post(
            self.URL, json={"amount": -100000}, headers=auth_headers,
        )
        assert resp.status_code in (400, 422)

    async def test_float_amount(self, client, auth_headers):
        resp = await client.post(
            self.URL, json={"amount": 100.5}, headers=auth_headers,
        )
        # Should either reject or truncate — never crash
        assert resp.status_code in (200, 400, 422)

    async def test_extremely_large_amount(self, client, auth_headers):
        resp = await client.post(
            self.URL,
            json={"amount": 999_999_999_999_999},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_string_amount(self, client, auth_headers):
        resp = await client.post(
            self.URL, json={"amount": "not_a_number"}, headers=auth_headers,
        )
        assert resp.status_code == 422