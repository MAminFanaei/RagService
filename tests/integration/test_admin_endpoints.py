# tests/integration/test_admin_endpoints.py
"""
Integration tests for Admin API endpoints.

All tests use httpx.AsyncClient matching the async FastAPI app.
"""

import pytest
from datetime import timedelta

from app.core.security import create_access_token

import uuid as _uuid_module
from sqlalchemy import select
from app.models.credit import MessageCredit
from app.payment.models.wallet import Wallet, WalletTransaction
from app.config import settings

FREE  = settings.FREE_MESSAGES_FOR_NEW_USERS
PRICE = settings.PRICE_PER_MESSAGE
ADMIN_PWD = "AdminPassword123!"   # matches ADMIN_PASSWORD in conftest


def credits_url(user_id: str) -> str:
    return f"/api/v1/admin/users/{user_id}/credits"


def wallet_url(user_id: str) -> str:
    return f"/api/v1/admin/users/{user_id}/wallet/topup"



class TestAdminUserManagement:
    @pytest.mark.asyncio
    async def test_list_users_as_admin(self, client, admin_headers, test_user):
        response = await client.get("/api/v1/admin/users", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert "total" in data
        assert data["total"] >= 1

    @pytest.mark.asyncio
    async def test_list_users_as_non_admin(self, client, auth_headers):
        response = await client.get("/api/v1/admin/users", headers=auth_headers)
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_list_users_unauthenticated(self, client):
        response = await client.get("/api/v1/admin/users")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_list_users_with_search(self, client, admin_headers, test_user):
        response = await client.get(
            "/api/v1/admin/users",
            headers=admin_headers,
            params={"search": test_user.email},
        )
        assert response.status_code == 200
        assert response.json()["total"] >= 1

    @pytest.mark.asyncio
    async def test_list_users_pagination(self, client, admin_headers):
        response = await client.get(
            "/api/v1/admin/users",
            headers=admin_headers,
            params={"skip": 0, "limit": 10},
        )
        assert response.status_code == 200
        data = response.json()
        assert "skip" in data
        assert "limit" in data

    @pytest.mark.asyncio
    async def test_get_user_details(self, client, admin_headers, test_user):
        response = await client.get(
            f"/api/v1/admin/users/{test_user.id}", headers=admin_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == test_user.id
        assert data["email"] == test_user.email
        assert "total_chats" in data
        assert "total_messages" in data

    @pytest.mark.asyncio
    async def test_get_user_details_non_admin(self, client, auth_headers, admin_user):
        response = await client.get(
            f"/api/v1/admin/users/{admin_user.id}", headers=auth_headers
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_get_nonexistent_user(self, client, admin_headers):
        response = await client.get(
            "/api/v1/admin/users/nonexistent-id", headers=admin_headers
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_user_settings(self, client, admin_headers, test_user):
        response = await client.patch(
            f"/api/v1/admin/users/{test_user.id}",
            headers=admin_headers,
            json={"max_messages_per_day": 500, "rate_limit_per_minute": 30},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["max_messages_per_day"] == 500
        assert data["rate_limit_per_minute"] == 30

    @pytest.mark.asyncio
    async def test_promote_to_admin(self, client, admin_headers, test_user):
        response = await client.patch(
            f"/api/v1/admin/users/{test_user.id}",
            headers=admin_headers,
            json={"is_admin": True},
        )
        assert response.status_code == 200
        assert response.json()["is_admin"] is True

    @pytest.mark.asyncio
    async def test_update_user_non_admin(self, client, auth_headers, admin_user):
        response = await client.patch(
            f"/api/v1/admin/users/{admin_user.id}",
            headers=auth_headers,
            json={"max_messages_per_day": 1000},
        )
        assert response.status_code == 403


class TestAdminDisableEnable:
    @pytest.mark.asyncio
    async def test_disable_user(self, client, admin_headers, test_user):
        response = await client.post(
            f"/api/v1/admin/users/{test_user.id}/disable",
            headers=admin_headers,
            json={"reason": "Test disable"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["is_active"] is False
        assert data["user_id"] == test_user.id

    @pytest.mark.asyncio
    async def test_disable_without_reason(self, client, admin_headers, test_user):
        response = await client.post(
            f"/api/v1/admin/users/{test_user.id}/disable",
            headers=admin_headers,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_disable_admin_forbidden(self, client, admin_headers, db):
        from app.models.user import User, AuthProvider
        from app.core.security import get_password_hash

        other_admin = User(
            email="other_admin@example.com",
            username="otheradmin",
            hashed_password=get_password_hash("AdminPass123!"),
            auth_provider=AuthProvider.LOCAL,
            is_active=True,
            is_admin=True,
        )
        db.add(other_admin)
        await db.flush()
        await db.refresh(other_admin)

        response = await client.post(
            f"/api/v1/admin/users/{other_admin.id}/disable",
            headers=admin_headers,
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_disable_self_fails(self, client, admin_headers, admin_user):
        response = await client.post(
            f"/api/v1/admin/users/{admin_user.id}/disable",
            headers=admin_headers,
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_enable_user(self, client, admin_headers, inactive_user):
        response = await client.post(
            f"/api/v1/admin/users/{inactive_user.id}/enable",
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["is_active"] is True

    @pytest.mark.asyncio
    async def test_enable_non_admin(self, client, auth_headers, inactive_user):
        response = await client.post(
            f"/api/v1/admin/users/{inactive_user.id}/enable",
            headers=auth_headers,
        )
        assert response.status_code == 403


class TestAdminDeletion:
    @pytest.mark.asyncio
    async def test_delete_user(self, client, admin_headers, db):
        from app.models.user import User, AuthProvider
        from app.core.security import get_password_hash

        target = User(
            email="todelete@example.com",
            username="todelete",
            hashed_password=get_password_hash("TestPass123!"),
            auth_provider=AuthProvider.LOCAL,
            is_active=True,
            is_admin=False,
        )
        db.add(target)
        await db.flush()
        await db.refresh(target)

        response = await client.request(
            "DELETE",
            f"/api/v1/admin/users/{target.id}",
            headers=admin_headers,
            json={
                "admin_password": "AdminPassword123!",
                "confirm_username": target.username,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == target.id
        assert "chats_deleted" in data
        assert "messages_deleted" in data

    @pytest.mark.asyncio
    async def test_delete_wrong_admin_password(self, client, admin_headers, test_user):
        """Test that wrong admin password returns 400"""
        response = await client.request(
            "DELETE",
            f"/api/v1/admin/users/{test_user.id}",
            headers=admin_headers,
            json={
                "admin_password": "WrongPassword123!",
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_nonexistent_user(self, client, admin_headers):
        """Test that deleting non-existent user returns 404"""
        response = await client.request(
            "DELETE",
            f"/api/v1/admin/users/nonexistent-user-id-12345",
            headers=admin_headers,
            json={
                "admin_password": "AdminPassword123!",
            },
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_self_fails(self, client, admin_headers, admin_user):
        response = await client.request(
            "DELETE",
            f"/api/v1/admin/users/{admin_user.id}",
            headers=admin_headers,
            json={
                "admin_password": "AdminPassword123!",
                "confirm_username": admin_user.username,
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, client, admin_headers):
        response = await client.request(
            "DELETE",
            "/api/v1/admin/users/nonexistent-id",
            headers=admin_headers,
            json={
                "admin_password": "AdminPassword123!",
                "confirm_username": "nonexistent",
            },
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_non_admin(self, client, auth_headers, admin_user):
        response = await client.request(
            "DELETE",
            f"/api/v1/admin/users/{admin_user.id}",
            headers=auth_headers,
            json={
                "admin_password": "TestPassword123!",
                "confirm_username": admin_user.username,
            },
        )
        assert response.status_code == 403


class TestAdminConversationExport:
    @pytest.mark.asyncio
    async def test_export_user_conversations(self, client, admin_headers, test_user, test_chat):
        response = await client.get(
            f"/api/v1/admin/users/{test_user.id}/conversations",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        conv = data[0]
        assert "chat_id" in conv
        assert "user_id" in conv
        assert "messages" in conv

    @pytest.mark.asyncio
    async def test_export_non_admin(self, client, auth_headers, admin_user):
        response = await client.get(
            f"/api/v1/admin/users/{admin_user.id}/conversations",
            headers=auth_headers,
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_export_nonexistent_user(self, client, admin_headers):
        response = await client.get(
            "/api/v1/admin/users/nonexistent-id/conversations",
            headers=admin_headers,
        )
        assert response.status_code == 404


class TestAdminSystemStats:
    @pytest.mark.asyncio
    async def test_get_system_stats(self, client, admin_headers):
        response = await client.get(
            "/api/v1/admin/stats/user_usage", headers=admin_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert "chats" in data
        assert "messages" in data
        assert "total" in data["users"]
        assert "active" in data["users"]
        assert "total" in data["messages"]
        assert "today" in data["messages"]

    @pytest.mark.asyncio
    async def test_system_stats_non_admin(self, client, auth_headers):
        response = await client.get(
            "/api/v1/admin/stats/user_usage", headers=auth_headers
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_system_stats_unauthenticated(self, client):
        response = await client.get("/api/v1/admin/stats/user_usage")
        assert response.status_code == 403


class TestAdminEdgeCases:
    @pytest.mark.asyncio
    async def test_large_skip_returns_empty(self, client, admin_headers):
        response = await client.get(
            "/api/v1/admin/users",
            headers=admin_headers,
            params={"skip": 999999999},
        )
        assert response.status_code == 200
        assert response.json()["users"] == []

    @pytest.mark.asyncio
    async def test_negative_skip_rejected(self, client, admin_headers):
        response = await client.get(
            "/api/v1/admin/users",
            headers=admin_headers,
            params={"skip": -1},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_zero_limit_rejected(self, client, admin_headers):
        response = await client.get(
            "/api/v1/admin/users",
            headers=admin_headers,
            params={"limit": 0},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_expired_admin_token(self, client, admin_user):
        expired = create_access_token(
            {"sub": admin_user.id, "email": admin_user.email, "is_admin": True},
            expires_delta=timedelta(seconds=-1),
        )
        response = await client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {expired}"},
        )
        assert response.status_code == 401

# ─────────────────────────────────────────────────────────────
# POST /admin/users/{user_id}/credits
# ─────────────────────────────────────────────────────────────

class TestAdminAddCredits:

    # ── Auth & Authorization ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_requires_authentication(self, client, test_user):
        """No token → 403."""
        response = await client.post(
            credits_url(test_user.id),
            json={"amount": 10, "reason": "test", "admin_password": ADMIN_PWD},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_requires_admin_role(self, client, auth_headers, test_user):
        """Regular user token → 403."""
        response = await client.post(
            credits_url(test_user.id),
            json={"amount": 10, "reason": "test", "admin_password": ADMIN_PWD},
            headers=auth_headers,
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_can_access(self, client, admin_headers, test_user, db):
        """Admin token + correct password → 200."""
        response = await client.post(
            credits_url(test_user.id),
            json={"amount": 10, "reason": "promotional grant", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_wrong_admin_password_returns_400(self, client, admin_headers, test_user):
        """
        Correct admin JWT but wrong password → 400.
        The verify_password check must fire even though the token is valid.
        """
        response = await client.post(
            credits_url(test_user.id),
            json={"amount": 10, "reason": "test", "admin_password": "WrongPassword999!"},
            headers=admin_headers,
        )
        assert response.status_code == 400

    # ── Input Validation ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_missing_amount_returns_422(self, client, admin_headers, test_user):
        """amount is required — missing → Pydantic 422."""
        response = await client.post(
            credits_url(test_user.id),
            json={"reason": "no amount", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_reason_returns_422(self, client, admin_headers, test_user):
        """reason is required — missing → Pydantic 422."""
        response = await client.post(
            credits_url(test_user.id),
            json={"amount": 10, "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_admin_password_returns_422(self, client, admin_headers, test_user):
        """admin_password is required — missing → Pydantic 422."""
        response = await client.post(
            credits_url(test_user.id),
            json={"amount": 10, "reason": "no password"},
            headers=admin_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_zero_amount_returns_422(self, client, admin_headers, test_user):
        """amount=0 violates ge=1 → 422."""
        response = await client.post(
            credits_url(test_user.id),
            json={"amount": 0, "reason": "zero", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_negative_amount_returns_422(self, client, admin_headers, test_user):
        """Negative amount → 422."""
        response = await client.post(
            credits_url(test_user.id),
            json={"amount": -5, "reason": "negative", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_above_maximum_amount_returns_422(self, client, admin_headers, test_user):
        """amount > 10000 violates le=10000 → 422."""
        response = await client.post(
            credits_url(test_user.id),
            json={"amount": 99999, "reason": "too many", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_reason_returns_422(self, client, admin_headers, test_user):
        """Empty string reason violates min_length=1 → 422."""
        response = await client.post(
            credits_url(test_user.id),
            json={"amount": 10, "reason": "", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_nonexistent_user_returns_404(self, client, admin_headers):
        """Unknown user_id → NotFoundException → 404."""
        fake_id = str(_uuid_module.uuid4())
        response = await client.post(
            credits_url(fake_id),
            json={"amount": 10, "reason": "ghost user", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 404

    # ── Response Shape ────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_response_has_all_required_fields(
        self, client, admin_headers, test_user, db
    ):
        """Response must contain all 5 fields from AdminCreditAdjustResponse."""
        response = await client.post(
            credits_url(test_user.id),
            json={"amount": 10, "reason": "shape check", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "user_id" in data
        assert "credits_added" in data
        assert "new_remaining" in data
        assert "total_purchased" in data

    @pytest.mark.asyncio
    async def test_response_user_id_matches_target(
        self, client, admin_headers, test_user, db
    ):
        """user_id in response must be the target user, not the admin."""
        response = await client.post(
            credits_url(test_user.id),
            json={"amount": 10, "reason": "id check", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["user_id"] == test_user.id

    @pytest.mark.asyncio
    async def test_response_credits_added_matches_request(
        self, client, admin_headers, test_user, db
    ):
        """credits_added in response must equal what was requested."""
        amount = 25
        response = await client.post(
            credits_url(test_user.id),
            json={"amount": amount, "reason": "amount echo", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["credits_added"] == amount

    # ── DB Consistency ────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_new_user_gets_free_plus_granted(
        self, client, admin_headers, test_user, db
    ):
        """
        User has no credit record yet.
        get_or_create gives FREE messages, grant adds on top.
        new_remaining must be FREE + amount.
        """
        amount = 10
        response = await client.post(
            credits_url(test_user.id),
            json={"amount": amount, "reason": "new user grant", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["new_remaining"] == FREE + amount

    @pytest.mark.asyncio
    async def test_existing_user_credits_stack(
        self, client, admin_headers, test_user, db
    ):
        """
        User already has a credit record.
        Grant must add on top of existing remaining, not overwrite it.
        """
        existing_remaining = 7
        credit = MessageCredit(
            user_id=test_user.id,
            remaining=existing_remaining,
            total_purchased=0,
            total_used=0,
            rejected_count=0,
        )
        db.add(credit)
        await db.flush()

        amount = 5
        response = await client.post(
            credits_url(test_user.id),
            json={"amount": amount, "reason": "stack check", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["new_remaining"] == existing_remaining + amount

    @pytest.mark.asyncio
    async def test_db_remaining_updated_after_grant(
        self, client, admin_headers, test_user, db
    ):
        """
        DB row must reflect the new remaining after the HTTP call.
        Guards against returning a fake value without committing.
        """
        amount = 15
        response = await client.post(
            credits_url(test_user.id),
            json={"amount": amount, "reason": "db verify", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200

        result = await db.execute(
            select(MessageCredit).where(MessageCredit.user_id == test_user.id)
        )
        credit = result.scalar_one_or_none()
        assert credit is not None, "Credit record was not created in DB"
        assert credit.remaining == FREE + amount

    @pytest.mark.asyncio
    async def test_total_purchased_incremented_in_db(
        self, client, admin_headers, test_user, db
    ):
        """total_purchased in DB must increase by the granted amount."""
        amount = 20
        response = await client.post(
            credits_url(test_user.id),
            json={"amount": amount, "reason": "total_purchased check", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200

        result = await db.execute(
            select(MessageCredit).where(MessageCredit.user_id == test_user.id)
        )
        credit = result.scalar_one_or_none()
        assert credit is not None, "Credit record was not created in DB"
        assert credit.total_purchased == amount

    @pytest.mark.asyncio
    async def test_wallet_is_not_touched(
        self, client, admin_headers, test_user, test_wallet, db
    ):
        """
        Admin credit grant must NOT debit the wallet.
        This is a free grant — wallet balance must be unchanged.
        """
        balance_before = test_wallet.balance

        response = await client.post(
            credits_url(test_user.id),
            json={"amount": 50, "reason": "wallet isolation", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200

        db.expire(test_wallet)
        result = await db.execute(
            select(Wallet).where(Wallet.user_id == test_user.id)
        )
        wallet_after = result.scalar_one()
        assert wallet_after.balance == balance_before

    @pytest.mark.asyncio
    async def test_two_grants_stack_correctly(
        self, client, admin_headers, test_user, db
    ):
        """Two sequential grants must stack: remaining = FREE + grant1 + grant2."""
        grant1, grant2 = 10, 15

        resp1 = await client.post(
            credits_url(test_user.id),
            json={"amount": grant1, "reason": "first grant", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert resp1.status_code == 200

        resp2 = await client.post(
            credits_url(test_user.id),
            json={"amount": grant2, "reason": "second grant", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert resp2.status_code == 200
        assert resp2.json()["new_remaining"] == FREE + grant1 + grant2

    @pytest.mark.asyncio
    async def test_grant_does_not_affect_other_users(
        self, client, admin_headers, test_user, other_user, db
    ):
        """
        Granting to test_user must not change other_user's record.
        Guards against a missing WHERE clause in the UPDATE.
        """
        other_credit = MessageCredit(
            user_id=other_user.id,
            remaining=FREE,
            total_purchased=0,
            total_used=0,
            rejected_count=0,
        )
        db.add(other_credit)
        await db.flush()

        response = await client.post(
            credits_url(test_user.id),
            json={"amount": 99, "reason": "isolation check", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200

        result = await db.execute(
            select(MessageCredit).where(MessageCredit.user_id == other_user.id)
        )
        other_after = result.scalar_one()
        assert other_after.remaining == FREE
        assert other_after.total_purchased == 0

    @pytest.mark.asyncio
    async def test_response_new_remaining_matches_db(
        self, client, admin_headers, test_user, db
    ):
        """
        new_remaining in response must match actual DB value.
        Guards against the stale-read bug.
        """
        amount = 12
        response = await client.post(
            credits_url(test_user.id),
            json={"amount": amount, "reason": "stale read guard", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200
        reported_remaining = response.json()["new_remaining"]

        result = await db.execute(
            select(MessageCredit).where(MessageCredit.user_id == test_user.id)
        )
        credit = result.scalar_one_or_none()
        assert credit is not None
        assert reported_remaining == credit.remaining


# ─────────────────────────────────────────────────────────────
# POST /admin/users/{user_id}/wallet/topup
# ─────────────────────────────────────────────────────────────

class TestAdminWalletTopup:

    # ── Auth & Authorization ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_requires_authentication(self, client, test_user):
        """No token → 403."""
        response = await client.post(
            wallet_url(test_user.id),
            json={"amount": 10000, "reason": "test", "admin_password": ADMIN_PWD},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_requires_admin_role(self, client, auth_headers, test_user):
        """Regular user token → 403."""
        response = await client.post(
            wallet_url(test_user.id),
            json={"amount": 10000, "reason": "test", "admin_password": ADMIN_PWD},
            headers=auth_headers,
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_can_access(self, client, admin_headers, test_user, db):
        """Admin token + correct password → 200."""
        response = await client.post(
            wallet_url(test_user.id),
            json={"amount": 10000, "reason": "support topup", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_wrong_admin_password_returns_400(self, client, admin_headers, test_user):
        """Correct JWT but wrong password → 400."""
        response = await client.post(
            wallet_url(test_user.id),
            json={"amount": 10000, "reason": "test", "admin_password": "WrongPassword999!"},
            headers=admin_headers,
        )
        assert response.status_code == 400

    # ── Input Validation ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_missing_amount_returns_422(self, client, admin_headers, test_user):
        response = await client.post(
            wallet_url(test_user.id),
            json={"reason": "no amount", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_reason_returns_422(self, client, admin_headers, test_user):
        response = await client.post(
            wallet_url(test_user.id),
            json={"amount": 10000, "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_admin_password_returns_422(self, client, admin_headers, test_user):
        """admin_password is required — missing → 422."""
        response = await client.post(
            wallet_url(test_user.id),
            json={"amount": 10000, "reason": "no password"},
            headers=admin_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_zero_amount_returns_422(self, client, admin_headers, test_user):
        response = await client.post(
            wallet_url(test_user.id),
            json={"amount": 0, "reason": "zero", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_negative_amount_returns_422(self, client, admin_headers, test_user):
        response = await client.post(
            wallet_url(test_user.id),
            json={"amount": -1000, "reason": "negative", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_reason_returns_422(self, client, admin_headers, test_user):
        response = await client.post(
            wallet_url(test_user.id),
            json={"amount": 10000, "reason": "", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_nonexistent_user_returns_404(self, client, admin_headers):
        """Unknown user_id → 404."""
        fake_id = str(_uuid_module.uuid4())
        response = await client.post(
            wallet_url(fake_id),
            json={"amount": 10000, "reason": "ghost", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 404

    # ── Response Shape ────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_response_has_all_required_fields(
        self, client, admin_headers, test_user, db
    ):
        """Response must contain all 5 fields from AdminWalletTopUpResponse."""
        response = await client.post(
            wallet_url(test_user.id),
            json={"amount": 5000, "reason": "shape check", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "user_id" in data
        assert "amount_added" in data
        assert "new_balance" in data
        assert "wallet_id" in data

    @pytest.mark.asyncio
    async def test_response_amount_added_matches_request(
        self, client, admin_headers, test_user, db
    ):
        amount = 75000
        response = await client.post(
            wallet_url(test_user.id),
            json={"amount": amount, "reason": "amount echo", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["amount_added"] == amount

    @pytest.mark.asyncio
    async def test_response_user_id_matches_target(
        self, client, admin_headers, test_user, db
    ):
        response = await client.post(
            wallet_url(test_user.id),
            json={"amount": 5000, "reason": "user_id echo", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["user_id"] == test_user.id

    @pytest.mark.asyncio
    async def test_wallet_id_is_valid_uuid(
        self, client, admin_headers, test_user, db
    ):
        """wallet_id must be a real UUID."""
        response = await client.post(
            wallet_url(test_user.id),
            json={"amount": 5000, "reason": "uuid check", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200
        wallet_id = response.json()["wallet_id"]
        parsed = _uuid_module.UUID(wallet_id)
        assert str(parsed) == wallet_id

    # ── DB Consistency ────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_no_wallet_auto_creates_and_tops_up(
        self, client, admin_headers, test_user, db
    ):
        """
        User has no wallet — WalletService.credit() creates one lazily.
        new_balance must equal the topup amount (started from 0).
        """
        amount = 50000
        response = await client.post(
            wallet_url(test_user.id),
            json={"amount": amount, "reason": "auto create", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["new_balance"] == amount

    @pytest.mark.asyncio
    async def test_existing_wallet_balance_increases(
        self, client, admin_headers, test_user, test_wallet, db
    ):
        """Topup must ADD to existing balance, not overwrite."""
        balance_before = test_wallet.balance
        amount = 30000

        response = await client.post(
            wallet_url(test_user.id),
            json={"amount": amount, "reason": "stack check", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["new_balance"] == balance_before + amount

    @pytest.mark.asyncio
    async def test_db_wallet_balance_updated(
        self, client, admin_headers, test_user, db
    ):
        """After the HTTP call, Wallet row in DB must reflect the new balance."""
        amount = 20000
        response = await client.post(
            wallet_url(test_user.id),
            json={"amount": amount, "reason": "db verify", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200

        result = await db.execute(
            select(Wallet).where(Wallet.user_id == test_user.id)
        )
        wallet = result.scalar_one_or_none()
        assert wallet is not None, "Wallet was not created in DB"
        assert wallet.balance == amount

    @pytest.mark.asyncio
    async def test_wallet_transaction_record_created(
        self, client, admin_headers, test_user, db
    ):
        """WalletService.credit() must create a WalletTransaction audit record."""
        amount = 15000
        response = await client.post(
            wallet_url(test_user.id),
            json={"amount": amount, "reason": "tx audit check", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200

        wallet_result = await db.execute(
            select(Wallet).where(Wallet.user_id == test_user.id)
        )
        wallet = wallet_result.scalar_one_or_none()
        assert wallet is not None, "Wallet not found in DB"

        tx_result = await db.execute(
            select(WalletTransaction).where(
                WalletTransaction.wallet_id == wallet.id
            )
        )
        transactions = tx_result.scalars().all()
        assert len(transactions) == 1
        assert transactions[0].amount == amount

    @pytest.mark.asyncio
    async def test_two_topups_stack_correctly(
        self, client, admin_headers, test_user, db
    ):
        """Two sequential topups → balance = topup1 + topup2."""
        t1, t2 = 10000, 25000

        resp1 = await client.post(
            wallet_url(test_user.id),
            json={"amount": t1, "reason": "first topup", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert resp1.status_code == 200

        resp2 = await client.post(
            wallet_url(test_user.id),
            json={"amount": t2, "reason": "second topup", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert resp2.status_code == 200
        assert resp2.json()["new_balance"] == t1 + t2

    @pytest.mark.asyncio
    async def test_two_topups_create_two_transaction_records(
        self, client, admin_headers, test_user, db
    ):
        """Each topup must create its own WalletTransaction row."""
        resp1 = await client.post(
            wallet_url(test_user.id),
            json={"amount": 10000, "reason": "tx count 1", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert resp1.status_code == 200

        resp2 = await client.post(
            wallet_url(test_user.id),
            json={"amount": 5000, "reason": "tx count 2", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert resp2.status_code == 200

        wallet_result = await db.execute(
            select(Wallet).where(Wallet.user_id == test_user.id)
        )
        wallet = wallet_result.scalar_one_or_none()
        assert wallet is not None

        tx_result = await db.execute(
            select(WalletTransaction).where(
                WalletTransaction.wallet_id == wallet.id
            )
        )
        transactions = tx_result.scalars().all()
        assert len(transactions) == 2

    @pytest.mark.asyncio
    async def test_topup_does_not_affect_other_users_wallet(
        self, client, admin_headers, test_user, other_user, db
    ):
        """Topping up test_user must not touch other_user's wallet."""
        other_wallet = Wallet(
            id=str(_uuid_module.uuid4()),
            user_id=other_user.id,
            balance=99999,
        )
        db.add(other_wallet)
        await db.flush()

        response = await client.post(
            wallet_url(test_user.id),
            json={"amount": 50000, "reason": "isolation check", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200

        db.expire(other_wallet)
        result = await db.execute(
            select(Wallet).where(Wallet.user_id == other_user.id)
        )
        other_after = result.scalar_one()
        assert other_after.balance == 99999

    @pytest.mark.asyncio
    async def test_topup_does_not_create_credit_record(
        self, client, admin_headers, test_user, db
    ):
        """
        Wallet topup must NOT create a MessageCredit record as a side effect.
        Credits and wallet are completely independent.
        """
        response = await client.post(
            wallet_url(test_user.id),
            json={"amount": 50000, "reason": "credit isolation", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200

        result = await db.execute(
            select(MessageCredit).where(MessageCredit.user_id == test_user.id)
        )
        credit = result.scalar_one_or_none()
        assert credit is None, "Topup must not create a credit record as a side effect"

    @pytest.mark.asyncio
    async def test_response_new_balance_matches_db(
        self, client, admin_headers, test_user, db
    ):
        """
        new_balance in response must match actual DB value.
        Guards against stale object inside WalletService.credit().
        """
        amount = 33000
        response = await client.post(
            wallet_url(test_user.id),
            json={"amount": amount, "reason": "balance consistency", "admin_password": ADMIN_PWD},
            headers=admin_headers,
        )
        assert response.status_code == 200
        reported_balance = response.json()["new_balance"]

        result = await db.execute(
            select(Wallet).where(Wallet.user_id == test_user.id)
        )
        wallet = result.scalar_one_or_none()
        assert wallet is not None
        assert reported_balance == wallet.balance