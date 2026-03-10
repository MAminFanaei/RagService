# tests/payment/test_units/test_discount_calculation.py
"""
Pure logic tests — no DB, no HTTP, no mocks.
These run in milliseconds.
"""

import pytest
from app.payment.models.discount import DiscountCode


class TestDiscountCalculation:
    """Test the calculate_discount method directly."""

    def _make_discount(self, dtype="PERCENTAGE", value=20, max_discount=None):
        """Create a minimal DiscountCode without touching SQLAlchemy."""
        class FakeDiscount:
            def __init__(self):
                self.discount_type = dtype
                self.discount_value = value
                self.max_discount = max_discount
            # Borrow the real method
            calculate_discount = DiscountCode.calculate_discount
        return FakeDiscount()

    def test_percentage_basic(self):
        d = self._make_discount("PERCENTAGE", 20)
        assert d.calculate_discount(100000) == 20000

    def test_percentage_with_cap(self):
        d = self._make_discount("PERCENTAGE", 50, max_discount=10000)
        assert d.calculate_discount(100000) == 10000  # Capped

    def test_percentage_100_percent(self):
        d = self._make_discount("PERCENTAGE", 100)
        assert d.calculate_discount(100000) == 100000

    def test_percentage_exceeds_amount(self):
        """Discount can never exceed the purchase amount."""
        d = self._make_discount("PERCENTAGE", 100, max_discount=999999)
        assert d.calculate_discount(5000) == 5000

    def test_fixed_basic(self):
        d = self._make_discount("FIXED", 30000)
        assert d.calculate_discount(100000) == 30000

    def test_fixed_exceeds_amount(self):
        """Fixed discount capped at purchase amount."""
        d = self._make_discount("FIXED", 200000)
        assert d.calculate_discount(100000) == 100000

    def test_fixed_zero_amount(self):
        d = self._make_discount("FIXED", 30000)
        assert d.calculate_discount(0) == 0

    def test_percentage_zero_amount(self):
        d = self._make_discount("PERCENTAGE", 20)
        assert d.calculate_discount(0) == 0

    def test_unknown_type_returns_zero(self):
        d = self._make_discount("UNKNOWN", 20)
        assert d.calculate_discount(100000) == 0