# tests/payment/test_units/test_callback_parsing.py
"""
Test CallbackData parsing with various SEP responses.
"""

import pytest
from app.payment.services.sep_client import CallbackData


class TestCallbackDataParsing:

    def test_normal_success(self):
        cb = CallbackData.from_form_data({
            "MID": "123", "State": "OK", "Status": "2",
            "RefNum": "REF123", "ResNum": "RES456",
            "Amount": "100000", "RRN": "999",
            "TraceNo": "111", "SecurePan": "6219****8080",
            "HashedCardNumber": "abc", "TerminalId": "123",
        })
        assert cb.is_ok is True
        assert cb.has_ref_num is True
        assert cb.status == 2
        assert cb.amount == 100000

    def test_canceled_by_user(self):
        cb = CallbackData.from_form_data({
            "State": "CanceledByUser", "Status": "1",
            "RefNum": "", "ResNum": "RES456",
        })
        assert cb.is_ok is False
        assert cb.has_ref_num is False

    def test_empty_ref_num_variations(self):
        """SEP may send empty string, whitespace, or None."""
        for ref in ["", "   ", None]:
            data = {"State": "OK", "Status": "2", "ResNum": "RES1"}
            if ref is not None:
                data["RefNum"] = ref
            cb = CallbackData.from_form_data(data)
            assert cb.has_ref_num is False

    def test_non_numeric_status(self):
        """SEP sends Status as string — handle gracefully."""
        cb = CallbackData.from_form_data({
            "State": "OK", "Status": "not_a_number",
            "ResNum": "RES1",
        })
        assert cb.status is None
        assert cb.is_ok is False  # status != 2

    def test_missing_fields(self):
        """Minimal data — should not crash."""
        cb = CallbackData.from_form_data({})
        assert cb.state is None
        assert cb.status is None
        assert cb.is_ok is False
        assert cb.has_ref_num is False

    def test_extra_fields_ignored(self):
        """SEP may add new fields in future — don't crash."""
        cb = CallbackData.from_form_data({
            "State": "OK", "Status": "2",
            "RefNum": "REF1", "ResNum": "RES1",
            "NewField2025": "whatever",
            "AnotherOne": "123",
        })
        assert cb.is_ok is True

    def test_amount_as_string(self):
        cb = CallbackData.from_form_data({"Amount": "500000"})
        assert cb.amount == 500000

    def test_special_characters_in_ref_num(self):
        """RefNum could contain unexpected characters."""
        cb = CallbackData.from_form_data({
            "RefNum": "REF/123+abc==%20",
            "State": "OK", "Status": "2", "ResNum": "R1",
        })
        assert cb.has_ref_num is True
        assert cb.ref_num == "REF/123+abc==%20"