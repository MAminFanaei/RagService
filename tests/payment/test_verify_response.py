# tests/payment/test_units/test_verify_response.py
"""
Test VerifyResponse parsing for all SEP result codes.
"""

import pytest
from app.payment.services.sep_client import VerifyResponse, VerifyTransactionDetail


class TestVerifyResponse:

    def test_success_with_detail(self):
        r = VerifyResponse.from_sep_response({
            "Success": True,
            "ResultCode": 0,
            "ResultDescription": "موفق",
            "TransactionDetail": {
                "RRN": "123", "RefNum": "REF1",
                "OrginalAmount": 100000,
                "AffectiveAmount": 100000,
                "MaskedPan": "6219****8080",
            },
        })
        assert r.is_successful is True
        assert r.verified_amount == 100000

    def test_duplicate_request(self):
        r = VerifyResponse.from_sep_response({
            "Success": True, "ResultCode": 2,
            "TransactionDetail": {"OrginalAmount": 100000},
        })
        assert r.is_duplicate is True
        assert r.is_successful is False  # ResultCode != 0

    def test_already_reversed(self):
        r = VerifyResponse.from_sep_response({
            "Success": False, "ResultCode": 5,
        })
        assert r.is_already_reversed is True

    def test_transaction_not_found(self):
        r = VerifyResponse.from_sep_response({
            "Success": False, "ResultCode": -2,
        })
        assert r.is_successful is False
        assert r.verified_amount is None

    def test_missing_transaction_detail(self):
        r = VerifyResponse.from_sep_response({
            "Success": True, "ResultCode": 0,
        })
        assert r.verified_amount is None

    def test_missing_amount_in_detail(self):
        r = VerifyResponse.from_sep_response({
            "Success": True, "ResultCode": 0,
            "TransactionDetail": {"RRN": "123"},
        })
        assert r.verified_amount is None