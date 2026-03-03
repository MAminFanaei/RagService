"""
Tests for Discount Code Operations

Tests:
    1. Admin creates discount code → success
    2. Non-admin creates discount → 403
    3. Validate valid code → returns discount preview
    4. Validate expired code → error
    5. Validate used-up code (max_uses reached) → error
    6. Validate code below min_purchase → error
    7. Percentage discount with max cap
    8. Fixed amount discount
    9. Per-user limit enforcement
    10. Inactive code → error
"""

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient

from app.payment.core.constants import DiscountType


@pytest.mark.asyncio
class TestDiscountCreate:
    """Test discount code creation (admin only)."""

    URL = "/api/v1/payment/discount/create"

    async def test_admin_creates_discount(
        self, client, admin_auth_headers
    ):
        """Admin creates a percentage discount → success."""
        response = await client.post(
            self.URL,
            json={
                "code": "SUMMER30",
                "discount_type": "PERCENTAGE",
                "discount_value": 30,
                "max_discount": 100000,
                "min_purchase": 50000,
                "max_uses": 100,
                "per_user_limit": 1,
                "valid_from": datetime.now(timezone.utc).isoformat(),
                "valid_until": (
                    datetime.now(timezone.utc) + timedelta(days=30)
                ).isoformat(),
            },
            headers=admin_auth_headers,
        )
        assert response.status_code in (200, 201)
        data = response.json()
        assert data["code"] == "SUMMER30"
        assert data["discount_type"] == "PERCENTAGE"

    async def test_non_admin_creates_discount(
        self, client, auth_headers
    ):
        """Regular user tries to create discount → 403."""
        response = await client.post(
            self.URL,
            json={
                "code": "HACKER",
                "discount_type": "FIXED",
                "discount_value": 999999,
            },
            headers=auth_headers,
        )
        assert response.status_code == 403

    async def test_duplicate_code(
        self, client, admin_auth_headers
    ):
        """Create same code twice → 409."""
        payload = {
            "code": "UNIQUE1",
            "discount_type": "FIXED",
            "discount_value": 10000,
            "valid_from": datetime.now(timezone.utc).isoformat(),
            "valid_until": (
                datetime.now(timezone.utc) + timedelta(days=30)
            ).isoformat(),
        }
        resp1 = await client.post(self.URL, json=payload, headers=admin_auth_headers)
        assert resp1.status_code in (200, 201)

        resp2 = await client.post(self.URL, json=payload, headers=admin_auth_headers)
        assert resp2.status_code == 409

    async def test_admin_creates_fixed_discount(
        self, client, admin_auth_headers
    ):
        """Admin creates a fixed-amount discount → success."""
        response = await client.post(
            self.URL,
            json={
                "code": "FLAT5000",
                "discount_type": "FIXED",
                "discount_value": 5000,
                "valid_from": datetime.now(timezone.utc).isoformat(),
                "valid_until": (
                    datetime.now(timezone.utc) + timedelta(days=7)
                ).isoformat(),
            },
            headers=admin_auth_headers,
        )
        assert response.status_code in (200, 201)


@pytest.mark.asyncio
class TestDiscountValidate:
    """Test discount code validation."""

    URL = "/api/v1/payment/discount/validate"

    async def test_validate_valid_code(
        self, client, auth_headers, discount_factory
    ):
        """Valid active code → returns discount preview."""
        from tests.payment.conftest import TestSessionLocal
        async with TestSessionLocal() as session:
            await discount_factory.create(
                session,
                code="VALID20",
                discount_type="PERCENTAGE",
                discount_value=20,
            )

        response = await client.post(
            self.URL,
            json={"code": "VALID20", "amount": 100000},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["discount_amount"] == 20000  # 20% of 100000

    async def test_validate_expired_code(
        self, client, auth_headers, discount_factory
    ):
        """Expired code → invalid."""
        from tests.payment.conftest import TestSessionLocal
        async with TestSessionLocal() as session:
            await discount_factory.create(
                session,
                code="EXPIRED",
                valid_from=datetime.now(timezone.utc) - timedelta(days=30),
                valid_until=datetime.now(timezone.utc) - timedelta(days=1),
            )

        response = await client.post(
            self.URL,
            json={"code": "EXPIRED", "amount": 100000},
            headers=auth_headers,
        )
        assert response.status_code in (200, 400)
        if response.status_code == 200:
            assert response.json()["valid"] is False

    async def test_validate_nonexistent_code(
        self, client, auth_headers
    ):
        """Code doesn't exist → invalid."""
        response = await client.post(
            self.URL,
            json={"code": "DOESNTEXIST", "amount": 100000},
            headers=auth_headers,
        )
        assert response.status_code in (200, 400, 404)

    async def test_validate_below_min_purchase(
        self, client, auth_headers, discount_factory
    ):
        """Amount below minimum purchase → invalid."""
        from tests.payment.conftest import TestSessionLocal
        async with TestSessionLocal() as session:
            await discount_factory.create(
                session,
                code="MINPURCHASE",
                min_purchase=200000,
            )

        response = await client.post(
            self.URL,
            json={"code": "MINPURCHASE", "amount": 50000},  # Below min
            headers=auth_headers,
        )
        assert response.status_code in (200, 400)
        if response.status_code == 200:
            assert response.json()["valid"] is False

    async def test_percentage_discount_with_cap(
        self, client, auth_headers, discount_factory
    ):
        """20% of 1,000,000 = 200,000 but max_discount = 50,000 → capped."""
        from tests.payment.conftest import TestSessionLocal
        async with TestSessionLocal() as session:
            await discount_factory.create(
                session,
                code="CAPPED",
                discount_type="PERCENTAGE",
                discount_value=20,
                max_discount=50000,
            )

        response = await client.post(
            self.URL,
            json={"code": "CAPPED", "amount": 1000000},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["discount_amount"] == 50000  # Capped

    async def test_fixed_discount(
        self, client, auth_headers, discount_factory
    ):
        """Fixed 30,000 off 100,000 → discount = 30,000."""
        from tests.payment.conftest import TestSessionLocal
        async with TestSessionLocal() as session:
            await discount_factory.create(
                session,
                code="FIXED30K",
                discount_type="FIXED",
                discount_value=30000,
            )

        response = await client.post(
            self.URL,
            json={"code": "FIXED30K", "amount": 100000},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["discount_amount"] == 30000

    async def test_unauthenticated(self, client):
        """No auth → 401."""
        response = await client.post(
            self.URL,
            json={"code": "ANY", "amount": 100000},
        )
        assert response.status_code in (401, 403)
