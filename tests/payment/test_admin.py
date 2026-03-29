"""
Tests for Payment Admin Endpoints

Tests:
    1. Admin overview → returns stats (counts, revenue, alerts)
    2. Admin list payments → all users' payments, filtered
    3. Admin get payment detail → any user's payment
    4. Admin get payment reverses → any user's reverses
    5. Non-admin user → 403 on all admin endpoints
    6. Unauthenticated → 401 on all admin endpoints
    7. Edge cases: empty DB, filters, pagination
"""

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient

from app.payment.models.payment import Payment
from app.payment.models.reverse import Reverse
from app.payment.core.constants import PaymentStatus, ReverseStatus
from app.payment.config import payment_settings


# ─────────────────────────────────────────────────────────────
# Helper to create a second user (not the default test_user)
# ─────────────────────────────────────────────────────────────

async def create_other_user(payment_db):
    """Create a second regular user for multi-user tests."""
    from app.models.user import User
    user = User(
        id=str(uuid.uuid4()),
        username="otheruser",
        email="other@example.com",
        hashed_password="hashed_fake_password",
        is_active=True,
        is_admin=False,
        is_verified=True,
    )
    payment_db.add(user)
    await payment_db.flush()
    await payment_db.refresh(user)
    return user


async def create_reverse(payment_db, payment_id: str, ref_num: str,
                          amount: int, status=ReverseStatus.COMPLETED,
                          result_code: int = 0) -> Reverse:
    """Helper to create a reverse record."""
    reverse = Reverse(
        id=str(uuid.uuid4()),
        payment_id=payment_id,
        ref_num=ref_num,
        amount=amount,
        reason="test reverse",
        status=status,
        result_code=result_code,
        result_description="موفق" if result_code == 0 else "خطا",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    payment_db.add(reverse)
    await payment_db.flush()
    await payment_db.refresh(reverse)
    return reverse


# ═════════════════════════════════════════════════════════════
# ADMIN OVERVIEW
# ═════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestAdminOverview:
    """Test GET /api/v1/payment/admin/overview."""

    URL = "/api/v1/payment/admin/overview"

    async def test_overview_empty_db(
        self, client, admin_auth_headers,
    ):
        """Empty database → all zeros."""
        response = await client.get(self.URL, headers=admin_auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total_payments"] == 0
        assert data["total_verified_amount"] == 0
        assert "by_status" in data
        assert data["by_status"] == []
        assert data["stuck_callback_received"] == 0
        assert data["stuck_verify_timeout"] == 0
        assert data["amount_mismatches"] == 0
        assert data["recent_failures_24h"] == 0

    async def test_overview_with_payments(
        self, client, test_user, admin_auth_headers,
        payment_factory, payment_db,
    ):
        """Multiple payments in different states → correct counts."""
        # Create payments in various states
        await payment_factory.create(
            payment_db, user_id=test_user.id, amount=100000,
            status=PaymentStatus.VERIFIED,
            ref_num=f"REF_{uuid.uuid4().hex[:10]}",
        )
        await payment_factory.create(
            payment_db, user_id=test_user.id, amount=200000,
            status=PaymentStatus.VERIFIED,
            ref_num=f"REF_{uuid.uuid4().hex[:10]}",
        )
        await payment_factory.create(
            payment_db, user_id=test_user.id, amount=50000,
            status=PaymentStatus.FAILED,
        )
        await payment_factory.create(
            payment_db, user_id=test_user.id, amount=75000,
            status=PaymentStatus.PENDING,
        )

        response = await client.get(self.URL, headers=admin_auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total_payments"] == 4
        assert data["total_verified_amount"] == 300000  # 100k + 200k

        # by_status is a list of {status, count, total_amount}
        by_status = {s["status"]: s for s in data["by_status"]}
        assert by_status["VERIFIED"]["count"] == 2
        assert by_status["VERIFIED"]["total_amount"] == 300000
        assert by_status["FAILED"]["count"] == 1
        assert by_status["PENDING"]["count"] == 1

    async def test_overview_alerts_stuck_payments(
        self, client, test_user, admin_auth_headers,
        payment_factory, payment_db,
    ):
        """Payments stuck in CALLBACK_RECEIVED or VERIFY_TIMEOUT → appear in alerts."""
        await payment_factory.create(
            payment_db, user_id=test_user.id, amount=100000,
            status=PaymentStatus.CALLBACK_RECEIVED,
        )
        await payment_factory.create(
            payment_db, user_id=test_user.id, amount=200000,
            status=PaymentStatus.VERIFY_TIMEOUT,
        )

        response = await client.get(self.URL, headers=admin_auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["stuck_callback_received"] >= 1
        assert data["stuck_verify_timeout"] >= 1

    async def test_overview_non_admin_forbidden(
        self, client, auth_headers,
    ):
        """Regular user → 403."""
        response = await client.get(self.URL, headers=auth_headers)
        assert response.status_code == 403

    async def test_overview_unauthenticated(self, client):
        """No auth → 401."""
        response = await client.get(self.URL)
        assert response.status_code in (401, 403)


# ═════════════════════════════════════════════════════════════
# ADMIN LIST PAYMENTS
# ═════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestAdminListPayments:
    """Test GET /api/v1/payment/admin/payments."""

    URL = "/api/v1/payment/admin/payments"

    async def test_list_all_payments(
        self, client, test_user, admin_auth_headers,
        payment_factory, payment_db,
    ):
        """Admin sees ALL users' payments."""
        other_user = await create_other_user(payment_db)

        await payment_factory.create(
            payment_db, user_id=test_user.id, amount=100000,
        )
        await payment_factory.create(
            payment_db, user_id=other_user.id, amount=200000,
        )

        response = await client.get(self.URL, headers=admin_auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["payments"]) == 2

        # Both users' payments visible
        user_ids = {p["user_id"] for p in data["payments"]}
        assert test_user.id in user_ids
        assert other_user.id in user_ids

    async def test_filter_by_status(
        self, client, test_user, admin_auth_headers,
        payment_factory, payment_db,
    ):
        """Filter by status → only matching."""
        await payment_factory.create(
            payment_db, user_id=test_user.id, amount=100000,
            status=PaymentStatus.VERIFIED,
            ref_num=f"REF_{uuid.uuid4().hex[:10]}",
        )
        await payment_factory.create(
            payment_db, user_id=test_user.id, amount=50000,
            status=PaymentStatus.FAILED,
        )

        response = await client.get(
            f"{self.URL}?status=VERIFIED",
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["payments"][0]["status"] == "VERIFIED"

    async def test_filter_by_user_id(
        self, client, test_user, admin_auth_headers,
        payment_factory, payment_db,
    ):
        """Filter by user_id → only that user's payments."""
        other_user = await create_other_user(payment_db)

        await payment_factory.create(
            payment_db, user_id=test_user.id, amount=100000,
        )
        await payment_factory.create(
            payment_db, user_id=other_user.id, amount=200000,
        )

        response = await client.get(
            f"{self.URL}?user_id={test_user.id}",
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["payments"][0]["user_id"] == test_user.id

    async def test_pagination(
        self, client, test_user, admin_auth_headers,
        payment_factory, payment_db,
    ):
        """Pagination works: limit and offset."""
        for i in range(5):
            await payment_factory.create(
                payment_db, user_id=test_user.id,
                amount=(i + 1) * 10000,
            )

        # Page 1: first 2
        response = await client.get(
            f"{self.URL}?limit=2&offset=0",
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert len(data["payments"]) == 2

        # Page 2: next 2
        response = await client.get(
            f"{self.URL}?limit=2&offset=2",
            headers=admin_auth_headers,
        )
        data = response.json()
        assert len(data["payments"]) == 2

        # Page 3: last 1
        response = await client.get(
            f"{self.URL}?limit=2&offset=4",
            headers=admin_auth_headers,
        )
        data = response.json()
        assert len(data["payments"]) == 1

    async def test_empty_result(
        self, client, admin_auth_headers,
    ):
        """No payments → empty list."""
        response = await client.get(self.URL, headers=admin_auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["payments"] == []

    async def test_admin_sees_all_fields(
        self, client, test_user, admin_auth_headers,
        payment_factory, payment_db,
    ):
        """Admin response includes sensitive fields like user_id, failure_reason."""
        await payment_factory.create(
            payment_db, user_id=test_user.id, amount=100000,
            status=PaymentStatus.FAILED,
        )

        response = await client.get(self.URL, headers=admin_auth_headers)
        assert response.status_code == 200
        payment = response.json()["payments"][0]

        # These fields should be visible to admin
        assert "user_id" in payment
        assert "terminal_id" in payment
        assert "status" in payment
        assert "created_at" in payment

    async def test_non_admin_forbidden(
        self, client, auth_headers,
    ):
        """Regular user → 403."""
        response = await client.get(self.URL, headers=auth_headers)
        assert response.status_code == 403

    async def test_unauthenticated(self, client):
        """No auth → 401."""
        response = await client.get(self.URL)
        assert response.status_code in (401, 403)


# ═════════════════════════════════════════════════════════════
# ADMIN GET PAYMENT DETAIL
# ═════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestAdminGetPayment:
    """Test GET /api/v1/payment/admin/payments/{payment_id}."""

    def _url(self, pid: str) -> str:
        return f"/api/v1/payment/admin/payments/{pid}"

    async def test_get_any_users_payment(
        self, client, test_user, admin_auth_headers,
        payment_factory, payment_db,
    ):
        """Admin can view any user's payment."""
        other_user = await create_other_user(payment_db)
        payment = await payment_factory.create(
            payment_db, user_id=other_user.id, amount=250000,
            status=PaymentStatus.VERIFIED,
            ref_num=f"REF_{uuid.uuid4().hex[:10]}",
        )

        response = await client.get(
            self._url(payment.id), headers=admin_auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == payment.id
        assert data["user_id"] == other_user.id
        assert data["amount"] == 250000
        assert data["status"] == "VERIFIED"

    async def test_get_payment_full_detail(
        self, client, test_user, admin_auth_headers,
        payment_factory, payment_db,
    ):
        """Admin sees all SEP fields."""
        payment = await payment_factory.create(
            payment_db, user_id=test_user.id, amount=100000,
            status=PaymentStatus.VERIFIED,
            ref_num=f"REF_{uuid.uuid4().hex[:10]}",
        )

        response = await client.get(
            self._url(payment.id), headers=admin_auth_headers,
        )
        assert response.status_code == 200
        data = response.json()

        # Admin-only fields should be present
        expected_fields = [
            "id", "user_id", "res_num", "ref_num", "amount",
            "original_amount", "discount_amount", "status",
            "terminal_id", "token", "state", "status_code",
            "rrn", "trace_no", "secure_pan", "sep_result_code",
            "failure_reason", "created_at", "updated_at",
        ]
        for field in expected_fields:
            assert field in data, f"Missing field: {field}"

    async def test_nonexistent_payment(
        self, client, admin_auth_headers,
    ):
        """Payment doesn't exist → 404."""
        fake_id = str(uuid.uuid4())
        response = await client.get(
            self._url(fake_id), headers=admin_auth_headers,
        )
        assert response.status_code == 404

    async def test_non_admin_forbidden(
        self, client, test_user, auth_headers,
        payment_factory, payment_db,
    ):
        """Regular user → 403 even for their own payment."""
        payment = await payment_factory.create(
            payment_db, user_id=test_user.id, amount=100000,
        )
        response = await client.get(
            self._url(payment.id), headers=auth_headers,
        )
        assert response.status_code == 403

    async def test_unauthenticated(self, client):
        """No auth → 401."""
        response = await client.get(self._url("any-id"))
        assert response.status_code in (401, 403)


# ═════════════════════════════════════════════════════════════
# ADMIN LIST REVERSES
# ═════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestAdminListReverses:
    """Test GET /api/v1/payment/admin/payments/{payment_id}/reverses."""

    def _url(self, pid: str) -> str:
        return f"/api/v1/payment/admin/payments/{pid}/reverses"

    async def test_list_reverses_for_any_payment(
        self, client, test_user, admin_auth_headers,
        payment_factory, payment_db,
    ):
        """Admin can see reverses for any user's payment."""
        other_user = await create_other_user(payment_db)
        ref_num = f"REF_{uuid.uuid4().hex[:10]}"
        payment = await payment_factory.create(
            payment_db, user_id=other_user.id, amount=100000,
            status=PaymentStatus.REVERSED, ref_num=ref_num,
        )

        reverse = await create_reverse(
            payment_db, payment_id=payment.id,
            ref_num=ref_num, amount=100000,
            status=ReverseStatus.COMPLETED,
        )

        response = await client.get(
            self._url(payment.id), headers=admin_auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["payment_id"] == payment.id
        assert data["total"] >= 1
        assert len(data["reverses"]) >= 1

        rev = data["reverses"][0]
        assert rev["id"] == reverse.id
        assert rev["ref_num"] == ref_num
        assert rev["amount"] == 100000
        assert rev["status"] == "COMPLETED"

    async def test_list_reverses_empty(
        self, client, test_user, admin_auth_headers,
        payment_factory, payment_db,
    ):
        """Payment with no reverses → empty list."""
        payment = await payment_factory.create(
            payment_db, user_id=test_user.id, amount=100000,
        )

        response = await client.get(
            self._url(payment.id), headers=admin_auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["reverses"] == []

    async def test_multiple_reverse_attempts(
        self, client, test_user, admin_auth_headers,
        payment_factory, payment_db,
    ):
        """Multiple reverse attempts → all visible."""
        ref_num = f"REF_{uuid.uuid4().hex[:10]}"
        payment = await payment_factory.create(
            payment_db, user_id=test_user.id, amount=100000,
            status=PaymentStatus.VERIFIED, ref_num=ref_num,
        )

        # First attempt failed
        await create_reverse(
            payment_db, payment_id=payment.id,
            ref_num=ref_num, amount=100000,
            status=ReverseStatus.FAILED, result_code=-2,
        )
        # Second attempt succeeded
        await create_reverse(
            payment_db, payment_id=payment.id,
            ref_num=ref_num, amount=100000,
            status=ReverseStatus.COMPLETED, result_code=0,
        )

        response = await client.get(
            self._url(payment.id), headers=admin_auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        statuses = {r["status"] for r in data["reverses"]}
        assert "FAILED" in statuses
        assert "COMPLETED" in statuses

    async def test_nonexistent_payment(
        self, client, admin_auth_headers,
    ):
        """Payment doesn't exist → 404."""
        fake_id = str(uuid.uuid4())
        response = await client.get(
            self._url(fake_id), headers=admin_auth_headers,
        )
        assert response.status_code == 404

    async def test_non_admin_forbidden(
        self, client, test_user, auth_headers,
        payment_factory, payment_db,
    ):
        """Regular user → 403."""
        payment = await payment_factory.create(
            payment_db, user_id=test_user.id, amount=100000,
        )
        response = await client.get(
            self._url(payment.id), headers=auth_headers,
        )
        assert response.status_code == 403

    async def test_unauthenticated(self, client):
        """No auth → 401."""
        response = await client.get(self._url("any-id"))
        assert response.status_code in (401, 403)
