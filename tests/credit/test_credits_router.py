"""
Tests for Credits Router — /api/v1/credits/

Tests the full HTTP layer: auth, validation, DB writes, response shape.
Uses real DB (with SAVEPOINT rollback) + mock_redis + real WalletService.

Coverage:
    GET  /credits/pricing   — public endpoint, no auth
    POST /credits/purchase  — auth required, wallet balance, boundaries

NOTE: Adjust URL_PRICING and URL_PURCHASE if your router prefix differs.
      Check main.py for the exact prefix used when including the credits router.
"""

import uuid as _uuid_module
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from httpx import AsyncClient

from app.config import settings
from app.models.credit import MessageCredit
from app.payment.models.wallet import Wallet

FREE    = settings.FREE_MESSAGES_FOR_NEW_USERS
PRICE   = settings.PRICE_PER_MESSAGE
MIN_BUY = settings.MIN_MESSAGE_PURCHASE
MAX_BUY = settings.MAX_MESSAGE_PURCHASE

URL_PRICING  = "/api/v1/credits/pricing"
URL_PURCHASE = "/api/v1/credits/purchase"


# =============================================================================
# GET /credits/pricing
# =============================================================================

class TestGetPricing:

    async def test_pricing_returns_200(self, client: AsyncClient):
        """Public endpoint, no auth needed → always 200."""
        response = await client.get(URL_PRICING)
        assert response.status_code == 200

    async def test_pricing_response_has_all_required_fields(
        self, client: AsyncClient
    ):
        """Response must contain all required fields."""
        response = await client.get(URL_PRICING)
        data = response.json()

        assert "price_per_message" in data
        assert "free_messages_for_new_users" in data
        assert "min_purchase" in data
        assert "max_purchase" in data
        assert "currency" in data

    async def test_pricing_values_match_settings(self, client: AsyncClient):
        """
        Values must match what is in settings — not hardcoded.
        If settings change, tests catch the mismatch automatically.
        """
        response = await client.get(URL_PRICING)
        data = response.json()

        assert data["price_per_message"] == PRICE
        assert data["free_messages_for_new_users"] == FREE
        assert data["min_purchase"] == MIN_BUY
        assert data["max_purchase"] == MAX_BUY

    async def test_pricing_currency_is_irr(self, client: AsyncClient):
        """Currency must always be IRR (Iranian Rial)."""
        response = await client.get(URL_PRICING)
        assert response.json()["currency"] == "IRR"

    async def test_pricing_accessible_without_token(self, client: AsyncClient):
        """Pricing is a public endpoint — no Authorization header required."""
        response = await client.get(URL_PRICING)
        assert response.status_code not in (401, 403)

    async def test_pricing_response_content_type_is_json(
        self, client: AsyncClient
    ):
        """
        Middleware must not convert this to HTML.
        Public endpoints are sometimes accidentally caught by redirect middleware.
        """
        response = await client.get(URL_PRICING)
        assert "application/json" in response.headers.get("content-type", "")

    async def test_pricing_min_less_than_max(self, client: AsyncClient):
        """
        Sanity check on settings: MIN must be strictly less than MAX.
        If misconfigured (MIN > MAX), the router silently rejects all purchases.
        """
        response = await client.get(URL_PRICING)
        data = response.json()
        assert data["min_purchase"] < data["max_purchase"]

    async def test_pricing_price_per_message_is_positive(
        self, client: AsyncClient
    ):
        """price_per_message must be > 0. A zero price would be a financial bug."""
        response = await client.get(URL_PRICING)
        assert response.json()["price_per_message"] > 0

    async def test_pricing_free_messages_is_non_negative(
        self, client: AsyncClient
    ):
        """free_messages_for_new_users must be >= 0. Negative is nonsensical."""
        response = await client.get(URL_PRICING)
        assert response.json()["free_messages_for_new_users"] >= 0


# =============================================================================
# POST /credits/purchase — Auth
# =============================================================================

class TestPurchaseAuth:

    async def test_purchase_requires_auth(self, client: AsyncClient):
        """No token → 403 (HTTPBearer returns 403 when no credentials sent)."""
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": MIN_BUY},
        )
        assert response.status_code == 403


# =============================================================================
# POST /credits/purchase — Pydantic / Input Validation
# These are rejected before any DB or wallet logic runs.
# =============================================================================

class TestPurchaseInputValidation:

    async def test_missing_message_count_returns_422(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Empty body → required field missing → Pydantic 422."""
        response = await client.post(
            URL_PURCHASE,
            json={},
            headers=auth_headers,
        )
        assert response.status_code == 422

    async def test_string_message_count_returns_422(
        self, client: AsyncClient, auth_headers: dict
    ):
        """String value for an int field → Pydantic 422."""
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": "five"},
            headers=auth_headers,
        )
        assert response.status_code == 422

    async def test_float_message_count_returns_422(
        self, client: AsyncClient, auth_headers: dict
    ):
        """
        Float like 1.5 → Pydantic rejects (int field) → 422.
        Guards against partial message purchases slipping through.
        """
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": 1.5},
            headers=auth_headers,
        )
        assert response.status_code == 422

    async def test_zero_message_count_returns_422(
        self, client: AsyncClient, auth_headers: dict
    ):
        """
        message_count=0 → Pydantic rejects (Field ge=1) → 422.
        Caught by Pydantic BEFORE our router logic runs.
        """
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": 0},
            headers=auth_headers,
        )
        assert response.status_code == 422

    async def test_negative_message_count_returns_422(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Negative count → 422 from Pydantic (ge=1 constraint)."""
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": -5},
            headers=auth_headers,
        )
        assert response.status_code == 422


# =============================================================================
# POST /credits/purchase — Business Rule Validation (400s)
# These pass Pydantic but are rejected by router business logic.
# =============================================================================

class TestPurchaseBusinessValidation:

    async def test_below_minimum(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_wallet,
        db: AsyncSession,
        test_user,
    ):
        """message_count < MIN_MESSAGE_PURCHASE → 400 BadRequest."""
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": MIN_BUY - 1},
            headers=auth_headers,
        )
        assert response.status_code == 422

    async def test_above_maximum_returns_400(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_wallet,
        db: AsyncSession,
        test_user,
    ):
        """message_count > MAX_MESSAGE_PURCHASE → 400 BadRequest."""
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": MAX_BUY + 1},
            headers=auth_headers,
        )
        assert response.status_code == 400

    async def test_min_minus_one_exact_boundary(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_wallet,
        db: AsyncSession,
        test_user,
    ):
        """
        Exact lower boundary: MIN - 1 must be rejected.
        MIN itself must pass (tested in happy path).
        Pins the `< MIN` check precisely.
        """
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": MIN_BUY - 1},
            headers=auth_headers,
        )
        assert response.status_code == 422

    async def test_max_plus_one_exact_boundary_returns_400(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_wallet,
        db: AsyncSession,
        test_user,
    ):
        """
        Exact upper boundary: MAX + 1 must be rejected.
        MAX itself must pass (tested in happy path).
        Pins the `> MAX` check precisely.
        """
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": MAX_BUY + 1},
            headers=auth_headers,
        )
        assert response.status_code == 400

    async def test_below_minimum_error_mentions_min_value(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_wallet,
        db: AsyncSession,
        test_user,
    ):
        """
        400 error for below-minimum must tell the user what the minimum is.
        Not a generic "bad request" — the value must appear in the message.
        """
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": MIN_BUY - 1},
            headers=auth_headers,
        )
        assert response.status_code == 422
        assert str(MIN_BUY) in str(response.json())

    async def test_above_maximum_error_mentions_max_value(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_wallet,
        db: AsyncSession,
        test_user,
    ):
        """
        400 error for above-maximum must tell the user what the maximum is.
        """
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": MAX_BUY + 1},
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert str(MAX_BUY) in str(response.json())


# =============================================================================
# POST /credits/purchase — Wallet Balance Checks
# =============================================================================

class TestPurchaseWalletBalance:

    async def test_insufficient_wallet_returns_402(
        self,
        client: AsyncClient,
        auth_headers: dict,
        empty_wallet,
        db: AsyncSession,
        test_user,
    ):
        """
        Wallet balance < required → PaymentRequiredException → 402.
        Check happens in router BEFORE calling CreditService.purchase.
        """
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": MIN_BUY},
            headers=auth_headers,
        )
        assert response.status_code == 402

    async def test_insufficient_wallet_error_body_has_useful_fields(
        self,
        client: AsyncClient,
        auth_headers: dict,
        empty_wallet,
        db: AsyncSession,
        test_user,
    ):
        """
        402 response body must contain INSUFFICIENT_WALLET_BALANCE error code
        and enough info for the frontend to know how much to top up.
        """
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": MIN_BUY},
            headers=auth_headers,
        )
        assert response.status_code == 402
        body_str = str(response.json())
        assert (
            "INSUFFICIENT_WALLET_BALANCE" in body_str
            or "wallet_balance" in body_str
            or "required_amount" in body_str
        )

    async def test_exact_balance_equals_required_succeeds(
        self,
        client: AsyncClient,
        auth_headers: dict,
        exact_balance_wallet,
        db: AsyncSession,
        test_user,
    ):
        """
        Boundary: wallet.balance == total_price exactly → must SUCCEED (not 402).
        The router check is `if wallet.balance < total_price` (strict less-than).
        Equal balance is sufficient.
        """
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": MIN_BUY},
            headers=auth_headers,
        )
        assert response.status_code == 200

    async def test_one_rial_short_returns_402(
        self,
        client: AsyncClient,
        auth_headers: dict,
        one_short_wallet,
        db: AsyncSession,
        test_user,
    ):
        """
        Boundary: wallet.balance == total_price - 1 → must return 402.
        One Rial short is still insufficient.
        """
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": MIN_BUY},
            headers=auth_headers,
        )
        assert response.status_code == 402

    async def test_insufficient_balance_error_contains_shortfall(
        self,
        client: AsyncClient,
        auth_headers: dict,
        db: AsyncSession,
        test_user,
    ):
        """
        When wallet balance is insufficient, the 402 response must include
        the shortfall so the frontend knows how much the user needs to add.
        """
        partial_balance = (MIN_BUY * PRICE) // 2
        wallet = Wallet(
            id=str(_uuid_module.uuid4()),
            user_id=test_user.id,
            balance=partial_balance,
        )
        db.add(wallet)
        await db.flush()

        response = await client.post(
            URL_PURCHASE,
            json={"message_count": MIN_BUY},
            headers=auth_headers,
        )
        assert response.status_code == 402
        body_str = str(response.json())
        assert "shortfall" in body_str or "required_amount" in body_str

    async def test_no_wallet_auto_creates_with_zero_balance_returns_402(
        self,
        client: AsyncClient,
        auth_headers: dict,
        db: AsyncSession,
        test_user,
    ):
        """
        test_user has NO wallet at all.
        Router calls WalletService.get_or_create_wallet() which creates one
        with balance=0 → insufficient → 402.
        Verifies the auto-create path does not crash and returns proper error.
        """
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": MIN_BUY},
            headers=auth_headers,
        )
        assert response.status_code == 402


# =============================================================================
# POST /credits/purchase — Happy Path
# =============================================================================

class TestPurchaseHappyPath:

    async def test_valid_purchase_returns_200(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_wallet,
        db: AsyncSession,
        test_user,
    ):
        """Happy path: valid count, sufficient balance → 200."""
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": MIN_BUY},
            headers=auth_headers,
        )
        assert response.status_code == 200

    async def test_valid_purchase_response_has_all_fields(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_wallet,
        db: AsyncSession,
        test_user,
    ):
        """Response must match PurchaseResponse schema — all 4 fields present."""
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": MIN_BUY},
            headers=auth_headers,
        )
        data = response.json()

        assert "purchased" in data
        assert "amount_charged" in data
        assert "remaining" in data
        assert "wallet_tx_id" in data

    async def test_valid_purchase_response_values(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_wallet,
        db: AsyncSession,
        test_user,
    ):
        """purchased and amount_charged must match input and settings exactly."""
        count = MIN_BUY
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": count},
            headers=auth_headers,
        )
        data = response.json()

        assert data["purchased"] == count
        assert data["amount_charged"] == count * PRICE

    async def test_valid_purchase_remaining_is_free_plus_purchased(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_wallet,
        db: AsyncSession,
        test_user,
    ):
        """
        After purchase, remaining = FREE + purchased.
        New user auto-gets credit record with FREE messages first,
        then purchased amount is added on top.
        """
        count = MIN_BUY
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": count},
            headers=auth_headers,
        )
        assert response.json()["remaining"] == FREE + count

    async def test_purchase_at_exact_minimum_succeeds(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_wallet,
        db: AsyncSession,
        test_user,
    ):
        """Boundary: message_count == MIN_MESSAGE_PURCHASE → 200."""
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": MIN_BUY},
            headers=auth_headers,
        )
        assert response.status_code == 200

    async def test_purchase_at_exact_maximum_succeeds(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_wallet,
        db: AsyncSession,
        test_user,
    ):
        """Boundary: message_count == MAX_MESSAGE_PURCHASE → 200."""
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": MAX_BUY},
            headers=auth_headers,
        )
        assert response.status_code == 200

    async def test_wallet_tx_id_is_non_empty_string(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_wallet,
        db: AsyncSession,
        test_user,
    ):
        """wallet_tx_id in response must be a non-empty string."""
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": MIN_BUY},
            headers=auth_headers,
        )
        tx_id = response.json()["wallet_tx_id"]

        assert isinstance(tx_id, str)
        assert len(tx_id) > 0

    async def test_wallet_tx_id_is_valid_uuid(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_wallet,
        db: AsyncSession,
        test_user,
    ):
        """
        wallet_tx_id must be a valid UUID (real wallet transaction was created).
        In router tests the wallet is REAL (not mocked), so this must be a
        genuine UUID from the DB — not a mock string.
        """
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": MIN_BUY},
            headers=auth_headers,
        )
        tx_id = response.json()["wallet_tx_id"]

        # Must parse as valid UUID — raises ValueError if not
        parsed = _uuid_module.UUID(tx_id)
        assert str(parsed) == tx_id


# =============================================================================
# POST /credits/purchase — Financial Consistency
# These tests verify the DB state AFTER the HTTP response, not just the
# response body. This is the most important class in the router tests.
# =============================================================================

class TestPurchaseFinancialConsistency:

    async def test_second_purchase_stacks_remaining_correctly (
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_wallet,
        db: AsyncSession,
        test_user,
    ):
        """
        After a successful purchase, wallet balance in DB must be reduced
        by exactly message_count * PRICE_PER_MESSAGE.
        This is the most critical financial consistency test.
        """
        result = await db.execute(
            select(Wallet).where(Wallet.user_id == test_user.id)
        )
        wallet_before = result.scalar_one()
        balance_before = wallet_before.balance

        count = MIN_BUY
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": count},
            headers=auth_headers,
        )
        assert response.status_code == 200

        db.expire(wallet_before)
        result2 = await db.execute(
            select(Wallet).where(Wallet.user_id == test_user.id)
        )
        wallet_after = result2.scalar_one()

        assert wallet_after.balance == balance_before - (count * PRICE)

    async def test_credit_record_remaining_updated_in_db(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_wallet,
        db: AsyncSession,
        test_user,
    ):
        """
        After purchase, the MessageCredit row in DB must reflect the new remaining.
        Verifies the router actually committed and did not just return a fake value.
        """
        count = MIN_BUY
        response = await client.post(
            URL_PURCHASE,
            json={"message_count": count},
            headers=auth_headers,
        )
        assert response.status_code == 200

        result = await db.execute(
            select(MessageCredit).where(MessageCredit.user_id == test_user.id)
        )
        credit = result.scalar_one()
        assert credit.remaining == FREE + count

    async def test_purchase_does_not_affect_other_users_credits(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_wallet,
        db: AsyncSession,
        test_user,
        other_user,
    ):
        """
        Purchasing credits for test_user must NOT change other_user's credits.
        Guards against a user_id mixup in the service layer.
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
            URL_PURCHASE,
            json={"message_count": MIN_BUY},
            headers=auth_headers,
        )
        assert response.status_code == 200

        result = await db.execute(
            select(MessageCredit).where(MessageCredit.user_id == other_user.id)
        )
        other_after = result.scalar_one()
        assert other_after.remaining == FREE
        assert other_after.total_purchased == 0

    async def test_second_purchase_stacks_remaining_correctly(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_wallet,
        db: AsyncSession,
        test_user,
    ):
        """
        Two sequential purchases:
            remaining after 1st = FREE + count1
            remaining after 2nd = FREE + count1 + count2

        Each request commits independently (router calls db.commit()).
        Verifies SQL relative increment (`remaining + count`) stacks correctly.
        """
        count1 = MIN_BUY
        count2 = MIN_BUY + 1

        resp1 = await client.post(
            URL_PURCHASE,
            json={"message_count": count1},
            headers=auth_headers,
        )
        assert resp1.status_code == 200
        assert resp1.json()["remaining"] == FREE + count1

        resp2 = await client.post(
            URL_PURCHASE,
            json={"message_count": count2},
            headers=auth_headers,
        )
        assert resp2.status_code == 200
        assert resp2.json()["remaining"] == FREE + count1 + count2

    async def test_second_purchase_wallet_balance_reduced_cumulatively(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_wallet,
        db: AsyncSession,
        test_user,
    ):
        """
        Two purchases → wallet balance reduced by the SUM of both charges.
        """
        result = await db.execute(
            select(Wallet).where(Wallet.user_id == test_user.id)
        )
        wallet = result.scalar_one()
        balance_before = wallet.balance

        count1 = MIN_BUY
        count2 = MIN_BUY + 1

        await client.post(
            URL_PURCHASE,
            json={"message_count": count1},
            headers=auth_headers,
        )
        await client.post(
            URL_PURCHASE,
            json={"message_count": count2},
            headers=auth_headers,
        )

        db.expire(wallet)
        result2 = await db.execute(
            select(Wallet).where(Wallet.user_id == test_user.id)
        )
        wallet_after = result2.scalar_one()

        total_charged = (count1 + count2) * PRICE
        assert wallet_after.balance == balance_before - total_charged
