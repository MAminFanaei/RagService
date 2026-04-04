"""
Tests for CreditService

Tests the service layer directly — no HTTP, no router.
Uses the real async DB with SAVEPOINT rollback (from conftest).

Coverage:
    get_or_create   — creation, idempotency, edge cases
    consume_one     — happy path, empty credits, boundary, counter integrity
    record_rejection — free window, charge trigger, dict structure, full walk
    purchase        — wallet debit + credit add, error propagation, stacking
    get_info        — dict shape, values, post-mutation state

KNOWN BUG documented inline (do not fix tests, fix the service):
    record_rejection() returns `max(credit.remaining - 1, 0)` calculated
    from the stale ORM object fetched BEFORE the SQL UPDATE runs.
    This means `credits_remaining` in the return dict may be stale
    under concurrency. See test_no_credits_and_at_max_rejections.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.services.credit_service import CreditService
from app.models.credit import MessageCredit
from app.config import settings

# Pull into local names so tests are readable and settings-change-proof
FREE       = settings.FREE_MESSAGES_FOR_NEW_USERS
MAX_REJECT = settings.MAX_FREE_REJECTIONS
PRICE      = settings.PRICE_PER_MESSAGE
MIN_BUY    = settings.MIN_MESSAGE_PURCHASE
MAX_BUY    = settings.MAX_MESSAGE_PURCHASE


# =============================================================================
# get_or_create
# =============================================================================

class TestGetOrCreate:

    async def test_creates_new_record_for_new_user(
        self, db: AsyncSession, test_user
    ):
        """New user gets a credit record with FREE_MESSAGES_FOR_NEW_USERS."""
        credit = await CreditService.get_or_create(db, test_user.id)

        assert credit is not None
        assert credit.user_id == test_user.id
        assert credit.remaining == FREE
        assert credit.total_purchased == 0
        assert credit.total_used == 0
        assert credit.rejected_count == 0

    async def test_returns_existing_record_without_modifying(
        self, db: AsyncSession, test_credit
    ):
        """
        Calling get_or_create for a user who already has a record
        returns the SAME record and does NOT reset values.
        """
        # Manually dirty the record to confirm it is not reset
        test_credit.remaining = 999
        test_credit.total_used = 42
        await db.flush()

        credit = await CreditService.get_or_create(db, test_credit.user_id)

        assert credit.remaining == 999
        assert credit.total_used == 42

    async def test_idempotent_called_twice(self, db: AsyncSession, test_user):
        """Calling get_or_create twice results in exactly ONE row in DB."""
        await CreditService.get_or_create(db, test_user.id)
        await CreditService.get_or_create(db, test_user.id)

        result = await db.execute(
            select(MessageCredit).where(MessageCredit.user_id == test_user.id)
        )
        rows = result.scalars().all()
        assert len(rows) == 1

    async def test_different_users_get_separate_records(
        self, db: AsyncSession, test_user, other_user
    ):
        """Two different users get two separate credit records."""
        credit_a = await CreditService.get_or_create(db, test_user.id)
        credit_b = await CreditService.get_or_create(db, other_user.id)

        assert credit_a.user_id != credit_b.user_id
        assert credit_a.remaining == FREE
        assert credit_b.remaining == FREE

    async def test_returned_object_has_correct_user_id_as_pk(
        self, db: AsyncSession, test_user
    ):
        """
        user_id IS the primary key (no separate UUID).
        Verify the returned object has user_id set correctly after flush().
        Pins the contract that user_id is the PK, not a generated field.
        """
        credit = await CreditService.get_or_create(db, test_user.id)
        assert credit.user_id == test_user.id

    async def test_new_record_all_counters_are_zero(
        self, db: AsyncSession, test_user
    ):
        """
        All counter fields except `remaining` must start at exactly 0.
        If someone adds a wrong default to the model, this test catches it.
        """
        credit = await CreditService.get_or_create(db, test_user.id)
        assert credit.total_purchased == 0
        assert credit.total_used == 0
        assert credit.rejected_count == 0

    async def test_get_or_create_for_nonexistent_user_raises(
        self, db: AsyncSession
    ):
        """
        Calling get_or_create with a user_id that has no matching user row
        must raise an IntegrityError (FK violation) on flush().
        Documents that the service trusts callers to pass valid user IDs.
        """
        from sqlalchemy.exc import IntegrityError

        fake_user_id = "00000000-dead-beef-0000-000000000000"
        with pytest.raises(IntegrityError):
            await CreditService.get_or_create(db, fake_user_id)


# =============================================================================
# consume_one
# =============================================================================

class TestConsumeOne:

    async def test_decrements_remaining_by_one(
        self, db: AsyncSession, test_credit
    ):
        """Happy path: remaining goes from FREE to FREE - 1."""
        new_remaining = await CreditService.consume_one(db, test_credit.user_id)
        assert new_remaining == FREE - 1

    async def test_increments_total_used(
        self, db: AsyncSession, test_credit
    ):
        """total_used increases by 1 after consumption."""
        await CreditService.consume_one(db, test_credit.user_id)

        credit = await CreditService._get(db, test_credit.user_id)
        assert credit.total_used == 1

    async def test_resets_rejected_count_to_zero(
        self, db: AsyncSession, test_user
    ):
        """
        consume_one must reset rejected_count to 0 regardless of its current value.
        A successful answer resets the rejection window.
        """
        credit = MessageCredit(
            user_id=test_user.id,
            remaining=5,
            total_purchased=0,
            total_used=0,
            rejected_count=3,
        )
        db.add(credit)
        await db.flush()

        await CreditService.consume_one(db, test_user.id)

        refreshed = await CreditService._get(db, test_user.id)
        assert refreshed.rejected_count == 0

    async def test_returns_zero_when_no_credits(
        self, db: AsyncSession, zero_credit_user
    ):
        """
        consume_one on a user with 0 remaining must return 0.
        The SQL WHERE remaining > 0 guard prevents going negative.
        """
        result = await CreditService.consume_one(db, zero_credit_user.user_id)
        assert result == 0

    async def test_remaining_does_not_go_below_zero(
        self, db: AsyncSession, zero_credit_user
    ):
        """Calling consume_one repeatedly on 0 credits stays at 0."""
        await CreditService.consume_one(db, zero_credit_user.user_id)
        await CreditService.consume_one(db, zero_credit_user.user_id)

        credit = await CreditService._get(db, zero_credit_user.user_id)
        assert credit.remaining == 0

    async def test_last_credit_goes_to_zero(self, db: AsyncSession, test_user):
        """Boundary: remaining=1 → consume → remaining=0 (not -1)."""
        credit = MessageCredit(
            user_id=test_user.id,
            remaining=1,
            total_purchased=0,
            total_used=0,
            rejected_count=0,
        )
        db.add(credit)
        await db.flush()

        result = await CreditService.consume_one(db, test_user.id)
        assert result == 0

        db_credit = await CreditService._get(db, test_user.id)
        assert db_credit.remaining == 0

    async def test_returns_actual_db_value_not_stale(
        self, db: AsyncSession, test_user
    ):
        """
        consume_one re-reads the DB after the UPDATE.
        The returned value must match what is actually in the DB.
        """
        credit = MessageCredit(
            user_id=test_user.id,
            remaining=10,
            total_purchased=0,
            total_used=0,
            rejected_count=0,
        )
        db.add(credit)
        await db.flush()

        returned = await CreditService.consume_one(db, test_user.id)
        db_credit = await CreditService._get(db, test_user.id)

        assert returned == db_credit.remaining

    async def test_updated_at_is_written_on_consume(
        self, db: AsyncSession, test_user
    ):
        """
        The SQL UPDATE explicitly sets updated_at.
        Guards against accidentally dropping updated_at from the .values() dict.
        """
        from datetime import datetime

        credit = MessageCredit(
            user_id=test_user.id,
            remaining=5,
            total_purchased=0,
            total_used=0,
            rejected_count=0,
        )
        db.add(credit)
        await db.flush()

        await CreditService.consume_one(db, test_user.id)

        refreshed = await CreditService._get(db, test_user.id)
        assert refreshed.updated_at is not None
        assert isinstance(refreshed.updated_at, datetime)

    async def test_total_used_accumulates_from_nonzero_start(
        self, db: AsyncSession, test_user
    ):
        """
        total_used must increment from its current value, not reset to 1.
        User already used 50 messages and consumes 1 more → total_used = 51.
        """
        credit = MessageCredit(
            user_id=test_user.id,
            remaining=10,
            total_purchased=60,
            total_used=50,
            rejected_count=0,
        )
        db.add(credit)
        await db.flush()

        await CreditService.consume_one(db, test_user.id)

        refreshed = await CreditService._get(db, test_user.id)
        assert refreshed.total_used == 51

    async def test_consume_on_empty_does_not_increment_total_used(
        self, db: AsyncSession, test_user
    ):
        """
        When remaining=0 the WHERE clause blocks the entire UPDATE.
        total_used must NOT increment — the most important guard against
        phantom consumption.
        """
        credit = MessageCredit(
            user_id=test_user.id,
            remaining=0,
            total_purchased=0,
            total_used=10,
            rejected_count=0,
        )
        db.add(credit)
        await db.flush()

        await CreditService.consume_one(db, test_user.id)

        refreshed = await CreditService._get(db, test_user.id)
        assert refreshed.total_used == 10   # must be frozen

    async def test_consume_when_rejected_count_already_zero(
        self, db: AsyncSession, test_user
    ):
        """
        consume_one resets rejected_count to 0 unconditionally.
        Must not raise or error when rejected_count is already 0.
        """
        credit = MessageCredit(
            user_id=test_user.id,
            remaining=5,
            total_purchased=0,
            total_used=0,
            rejected_count=0,
        )
        db.add(credit)
        await db.flush()

        result = await CreditService.consume_one(db, test_user.id)
        assert result == 4

        refreshed = await CreditService._get(db, test_user.id)
        assert refreshed.rejected_count == 0

    async def test_consume_on_empty_does_not_reset_rejected_count(
        self, db: AsyncSession, test_user
    ):
        """
        When remaining=0 the full UPDATE is skipped.
        rejected_count must NOT be reset — the reset only happens on
        a SUCCESSFUL consumption.
        """
        credit = MessageCredit(
            user_id=test_user.id,
            remaining=0,
            total_purchased=0,
            total_used=0,
            rejected_count=3,
        )
        db.add(credit)
        await db.flush()

        await CreditService.consume_one(db, test_user.id)

        refreshed = await CreditService._get(db, test_user.id)
        assert refreshed.rejected_count == 3   # must be unchanged


# =============================================================================
# record_rejection
# =============================================================================

class TestRecordRejection:

    async def test_first_rejection_is_free(
        self, db: AsyncSession, test_credit
    ):
        """
        rejected_count starts at 0, which is < MAX_FREE_REJECTIONS.
        First rejection must NOT charge a credit.
        """
        result = await CreditService.record_rejection(db, test_credit.user_id)

        assert result["charged"] is False
        assert result["credits_remaining"] == FREE

    async def test_free_rejection_increments_counter(
        self, db: AsyncSession, test_credit
    ):
        """After a free rejection, rejected_count in DB is 1."""
        await CreditService.record_rejection(db, test_credit.user_id)

        credit = await CreditService._get(db, test_credit.user_id)
        assert credit.rejected_count == 1

    async def test_free_rejections_remaining_counts_down(
        self, db: AsyncSession, test_credit
    ):
        """
        free_rejections_remaining decrements with each free rejection.
        After 1st: MAX - 1. After 2nd: MAX - 2.
        """
        result1 = await CreditService.record_rejection(db, test_credit.user_id)
        assert result1["free_rejections_remaining"] == MAX_REJECT - 1

        result2 = await CreditService.record_rejection(db, test_credit.user_id)
        assert result2["free_rejections_remaining"] == MAX_REJECT - 2

    async def test_at_max_rejections_charges_credit(
        self, db: AsyncSession, at_max_rejections_credit
    ):
        """
        When rejected_count == MAX_FREE_REJECTIONS, the next rejection
        must charge 1 credit and reset the counter.
        """
        result = await CreditService.record_rejection(
            db, at_max_rejections_credit.user_id
        )
        assert result["charged"] is True

    async def test_at_max_rejections_counter_keeps_climbing(
        self, db: AsyncSession, at_max_rejections_credit
    ):
        """
        After being charged, rejected_count is NOT reset.
        It keeps incrementing — this is a lifetime counter.
        """
        await CreditService.record_rejection(db, at_max_rejections_credit.user_id)
        credit = await CreditService._get(db, at_max_rejections_credit.user_id)
        assert credit.rejected_count == MAX_REJECT + 1   # kept climbing, not reset

    async def test_at_max_rejections_decrements_remaining(
        self, db: AsyncSession, at_max_rejections_credit
    ):
        """After charge, DB remaining is FREE - 1."""
        await CreditService.record_rejection(db, at_max_rejections_credit.user_id)

        credit = await CreditService._get(db, at_max_rejections_credit.user_id)
        assert credit.remaining == FREE - 1

    async def test_charged_rejection_free_remaining_is_always_zero(
        self, db: AsyncSession, at_max_rejections_credit
    ):
        """
        Once lifetime quota is exhausted, free_rejections_remaining is
        always 0 — even after multiple charged rejections.
        It does NOT go negative.
        """
        result1 = await CreditService.record_rejection(
            db, at_max_rejections_credit.user_id
        )
        assert result1["free_rejections_remaining"] == 0

        result2 = await CreditService.record_rejection(
            db, at_max_rejections_credit.user_id
        )
        assert result2["free_rejections_remaining"] == 0  # still 0, not -1

    async def test_return_dict_always_has_required_keys(
        self, db: AsyncSession, test_credit
    ):
        result = await CreditService.record_rejection(db, test_credit.user_id)
        assert "charged" in result
        assert "free_rejections_remaining" in result
        assert "credits_remaining" in result

    async def test_no_credits_and_at_max_rejections(
        self, db: AsyncSession, test_user
    ):
        """
        Edge case: user has 0 credits AND is at MAX_FREE_REJECTIONS.
        charged=True but credits_remaining stays 0 (cannot go negative).

        KNOWN BUG NOTE:
            The service returns max(credit.remaining - 1, 0) using the stale
            ORM object loaded BEFORE the SQL UPDATE runs. The max(..., 0) clamp
            works correctly here but the value is stale, not a post-update read.
        """
        credit = MessageCredit(
            user_id=test_user.id,
            remaining=0,
            total_purchased=10,
            total_used=10,
            rejected_count=MAX_REJECT,
        )
        db.add(credit)
        await db.flush()

        result = await CreditService.record_rejection(db, test_user.id)

        assert result["charged"] is True
        assert result["credits_remaining"] == 0
        assert result["free_rejections_remaining"] == 0

    async def test_new_user_gets_created_on_first_rejection(
        self, db: AsyncSession, test_user
    ):
        """
        record_rejection calls get_or_create internally.
        A user with no credit record gets one created automatically.
        """
        result = await CreditService.record_rejection(db, test_user.id)

        assert isinstance(result, dict)
        assert "charged" in result

        credit = await CreditService._get(db, test_user.id)
        assert credit is not None

    async def test_last_free_rejection_boundary(
        self, db: AsyncSession, test_user
    ):
        """
        The most critical boundary: rejected_count == MAX_FREE_REJECTIONS - 1
        → this is the LAST free rejection (still < MAX, so not charged yet).

        Tests the >= operator in:
            if credit.rejected_count >= settings.MAX_FREE_REJECTIONS
        An off-by-one here means users get charged too early or too late.
        """
        credit = MessageCredit(
            user_id=test_user.id,
            remaining=FREE,
            total_purchased=0,
            total_used=0,
            rejected_count=MAX_REJECT - 1,
        )
        db.add(credit)
        await db.flush()

        result = await CreditService.record_rejection(db, test_user.id)

        assert result["charged"] is False
        assert result["free_rejections_remaining"] == 0   # used the last free one

        refreshed = await CreditService._get(db, test_user.id)
        assert refreshed.rejected_count == MAX_REJECT     # now AT the threshold
        assert refreshed.remaining == FREE                # no credit consumed

    async def test_after_last_free_next_is_charged(
        self, db: AsyncSession, test_user
    ):
        """
        Directly follows test_last_free_rejection_boundary.
        Once free_rejections_remaining hits 0, the very next call charges.
        No reset, no grace period.
        """
        credit = MessageCredit(
            user_id=test_user.id,
            remaining=FREE,
            total_purchased=0,
            total_used=0,
            rejected_count=MAX_REJECT,   # already at threshold
        )
        db.add(credit)
        await db.flush()

        result = await CreditService.record_rejection(db, test_user.id)
        assert result["charged"] is True
        assert result["free_rejections_remaining"] == 0

    async def test_full_counter_walk_then_charge(
        self, db: AsyncSession, test_user
    ):
        """
        Walk through all MAX free rejections, verify each is free and
        free_rejections_remaining decrements correctly.
        Then verify the very next call (step MAX+1) is charged.
        """
        credit = MessageCredit(
            user_id=test_user.id,
            remaining=FREE,
            total_purchased=0,
            total_used=0,
            rejected_count=0,
        )
        db.add(credit)
        await db.flush()

        # Steps 1..MAX are all free
        for step in range(1, MAX_REJECT + 1):
            result = await CreditService.record_rejection(db, test_user.id)
            assert result["charged"] is False, \
                f"Should NOT charge at step {step} (MAX={MAX_REJECT})"
            assert result["free_rejections_remaining"] == MAX_REJECT - step, \
                f"Wrong free_remaining at step {step}"

        # Step MAX+1 → lifetime quota gone → charged
        result = await CreditService.record_rejection(db, test_user.id)
        assert result["charged"] is True, \
            f"Should charge at step {MAX_REJECT + 1}"
        assert result["free_rejections_remaining"] == 0

    async def test_second_rejection_after_quota_exhausted_also_charges(
        self, db: AsyncSession, test_user
    ):
        """
        Once quota is gone it stays gone. Two consecutive charged rejections
        both return charged=True and free_rejections_remaining=0.
        """
        credit = MessageCredit(
            user_id=test_user.id,
            remaining=FREE,
            total_purchased=0,
            total_used=0,
            rejected_count=MAX_REJECT,
        )
        db.add(credit)
        await db.flush()

        result1 = await CreditService.record_rejection(db, test_user.id)
        result2 = await CreditService.record_rejection(db, test_user.id)

        assert result1["charged"] is True
        assert result2["charged"] is True
        assert result1["free_rejections_remaining"] == 0
        assert result2["free_rejections_remaining"] == 0

    async def test_free_rejection_does_not_change_remaining_in_db(
        self, db: AsyncSession, test_credit
    ):
        original_remaining = test_credit.remaining
        await CreditService.record_rejection(db, test_credit.user_id)
        refreshed = await CreditService._get(db, test_credit.user_id)
        assert refreshed.remaining == original_remaining

    async def test_total_used_increments_on_charge(
        self, db: AsyncSession, at_max_rejections_credit
    ):
        """
        When a rejection is charged, total_used must increment by 1
        (same SQL UPDATE path as consume_one).
        """
        original_used = at_max_rejections_credit.total_used
        await CreditService.record_rejection(db, at_max_rejections_credit.user_id)
        refreshed = await CreditService._get(db, at_max_rejections_credit.user_id)
        assert refreshed.total_used == original_used + 1

    async def test_total_used_does_not_increment_on_free_rejection(
        self, db: AsyncSession, test_credit
    ):
        """
        Free rejection → no credit consumed → total_used must NOT change.
        The UPDATE in the free branch only touches rejected_count and updated_at.
        """
        original_used = test_credit.total_used
        await CreditService.record_rejection(db, test_credit.user_id)
        refreshed = await CreditService._get(db, test_credit.user_id)
        assert refreshed.total_used == original_used

    async def test_new_user_first_rejection_is_always_free(
        self, db: AsyncSession, test_user
    ):
        """
        get_or_create always sets rejected_count=0.
        A brand-new user's very first rejection can NEVER be charged
        (0 < MAX_FREE_REJECTIONS always holds).
        This is a safety guarantee of the system design.
        """
        result = await CreditService.record_rejection(db, test_user.id)
        assert result["charged"] is False

# =============================================================================
# purchase
# =============================================================================

class TestPurchase:

    def _make_mock_wallet_tx(self, tx_id: str = "mock-tx-uuid-001"):
        """Build a fake WalletTransaction-like object."""
        tx = MagicMock()
        tx.id = tx_id
        return tx

    async def test_purchase_adds_credits(self, db: AsyncSession, test_credit):
        """
        Happy path: purchase MIN_BUY messages → remaining increases by MIN_BUY.
        WalletService.debit is mocked to isolate credit logic from wallet.
        """
        mock_tx = self._make_mock_wallet_tx()

        with patch(
            "app.services.credit_service.WalletService.debit",
            new=AsyncMock(return_value=mock_tx),
        ):
            await CreditService.purchase(db, test_credit.user_id, MIN_BUY)

        credit = await CreditService._get(db, test_credit.user_id)
        assert credit.remaining == FREE + MIN_BUY

    async def test_purchase_increments_total_purchased(
        self, db: AsyncSession, test_credit
    ):
        """total_purchased in DB increases by message_count."""
        mock_tx = self._make_mock_wallet_tx()

        with patch(
            "app.services.credit_service.WalletService.debit",
            new=AsyncMock(return_value=mock_tx),
        ):
            await CreditService.purchase(db, test_credit.user_id, MIN_BUY)

        credit = await CreditService._get(db, test_credit.user_id)
        assert credit.total_purchased == MIN_BUY

    async def test_purchase_returns_correct_dict_keys(
        self, db: AsyncSession, test_credit
    ):
        """Return dict must have all 4 required keys."""
        mock_tx = self._make_mock_wallet_tx("wallet-tx-999")

        with patch(
            "app.services.credit_service.WalletService.debit",
            new=AsyncMock(return_value=mock_tx),
        ):
            result = await CreditService.purchase(db, test_credit.user_id, MIN_BUY)

        assert "purchased" in result
        assert "amount_charged" in result
        assert "remaining" in result
        assert "wallet_tx_id" in result

    async def test_purchase_returns_correct_dict_values(
        self, db: AsyncSession, test_credit
    ):
        """Return dict values must match input and settings."""
        mock_tx = self._make_mock_wallet_tx("wallet-tx-999")

        with patch(
            "app.services.credit_service.WalletService.debit",
            new=AsyncMock(return_value=mock_tx),
        ):
            result = await CreditService.purchase(db, test_credit.user_id, MIN_BUY)

        assert result["purchased"] == MIN_BUY
        assert result["amount_charged"] == MIN_BUY * PRICE
        assert result["wallet_tx_id"] == "wallet-tx-999"

    async def test_purchase_amount_charged_is_count_times_price(
        self, db: AsyncSession, test_credit
    ):
        """amount_charged must always equal message_count * PRICE_PER_MESSAGE."""
        count = MIN_BUY * 3
        mock_tx = self._make_mock_wallet_tx()

        with patch(
            "app.services.credit_service.WalletService.debit",
            new=AsyncMock(return_value=mock_tx),
        ):
            result = await CreditService.purchase(db, test_credit.user_id, count)

        assert result["amount_charged"] == count * PRICE

    async def test_purchase_wallet_debit_raises_propagates(
        self, db: AsyncSession, test_credit
    ):
        """
        If WalletService.debit raises, the exception propagates
        and NO credits are added to the DB.
        """
        from app.payment.services.wallet_service import InsufficientBalanceException

        original_remaining = test_credit.remaining

        with patch(
            "app.services.credit_service.WalletService.debit",
            new=AsyncMock(side_effect=InsufficientBalanceException("Not enough")),
        ):
            with pytest.raises(InsufficientBalanceException):
                await CreditService.purchase(db, test_credit.user_id, MIN_BUY)

        credit = await CreditService._get(db, test_credit.user_id)
        assert credit.remaining == original_remaining

    async def test_purchase_creates_credit_record_if_not_exists(
        self, db: AsyncSession, test_user
    ):
        """
        purchase() calls get_or_create internally.
        A user with no credit record gets one created, then credits added.
        New user gets FREE + purchased.
        """
        mock_tx = self._make_mock_wallet_tx()

        with patch(
            "app.services.credit_service.WalletService.debit",
            new=AsyncMock(return_value=mock_tx),
        ):
            await CreditService.purchase(db, test_user.id, MIN_BUY)

        credit = await CreditService._get(db, test_user.id)
        assert credit.remaining == FREE + MIN_BUY

    async def test_debit_called_with_correct_amount(
        self, db: AsyncSession, test_credit
    ):
        """
        WalletService.debit must be called with amount = message_count * PRICE.
        This is the most important financial correctness test at the service level.
        If the pricing formula changes without updating both sides, this catches it.
        """
        count = MIN_BUY * 2
        expected_amount = count * PRICE
        mock_tx = self._make_mock_wallet_tx()
        mock_debit = AsyncMock(return_value=mock_tx)

        with patch(
            "app.services.credit_service.WalletService.debit",
            new=mock_debit,
        ):
            await CreditService.purchase(db, test_credit.user_id, count)

        mock_debit.assert_called_once()
        # Support both positional and keyword call styles
        call_args = mock_debit.call_args
        called_amount = call_args.kwargs.get("amount")
        if called_amount is None and len(call_args.args) > 2:
            called_amount = call_args.args[2]
        assert called_amount == expected_amount

    async def test_debit_called_with_correct_user_id(
        self, db: AsyncSession, test_credit
    ):
        """
        WalletService.debit must be called for the CORRECT user.
        Guards against accidentally using a hardcoded or wrong user_id.
        """
        mock_tx = self._make_mock_wallet_tx()
        mock_debit = AsyncMock(return_value=mock_tx)

        with patch(
            "app.services.credit_service.WalletService.debit",
            new=mock_debit,
        ):
            await CreditService.purchase(db, test_credit.user_id, MIN_BUY)

        call_args = mock_debit.call_args
        called_user_id = call_args.kwargs.get("user_id")
        if called_user_id is None and len(call_args.args) > 1:
            called_user_id = call_args.args[1]
        assert called_user_id == test_credit.user_id

    async def test_two_sequential_purchases_stack_correctly(
        self, db: AsyncSession, test_credit
    ):
        """
        Purchase 1: remaining = FREE + count1
        Purchase 2: remaining = FREE + count1 + count2

        The SQL UPDATE uses relative `remaining + message_count`,
        so stacking must work correctly across calls.
        """
        count1 = MIN_BUY
        count2 = MIN_BUY + 1

        with patch(
            "app.services.credit_service.WalletService.debit",
            new=AsyncMock(side_effect=[
                self._make_mock_wallet_tx("tx-001"),
                self._make_mock_wallet_tx("tx-002"),
            ]),
        ):
            await CreditService.purchase(db, test_credit.user_id, count1)
            await CreditService.purchase(db, test_credit.user_id, count2)

        credit = await CreditService._get(db, test_credit.user_id)
        assert credit.remaining == FREE + count1 + count2

    async def test_total_purchased_accumulates_across_purchases(
        self, db: AsyncSession, test_credit
    ):
        """
        total_purchased must be the SUM of all purchases, not just the last one.
        """
        count1 = MIN_BUY
        count2 = MIN_BUY + 2

        with patch(
            "app.services.credit_service.WalletService.debit",
            new=AsyncMock(side_effect=[
                self._make_mock_wallet_tx("tx-001"),
                self._make_mock_wallet_tx("tx-002"),
            ]),
        ):
            await CreditService.purchase(db, test_credit.user_id, count1)
            await CreditService.purchase(db, test_credit.user_id, count2)

        credit = await CreditService._get(db, test_credit.user_id)
        assert credit.total_purchased == count1 + count2

    async def test_any_debit_exception_leaves_credits_untouched(
        self, db: AsyncSession, test_credit
    ):
        """
        If debit raises ANY exception (network error, deadlock, etc.),
        the credit record must be completely untouched.
        """
        original_remaining = test_credit.remaining
        original_purchased = test_credit.total_purchased

        with patch(
            "app.services.credit_service.WalletService.debit",
            new=AsyncMock(side_effect=RuntimeError("unexpected wallet error")),
        ):
            with pytest.raises(RuntimeError):
                await CreditService.purchase(db, test_credit.user_id, MIN_BUY)

        credit = await CreditService._get(db, test_credit.user_id)
        assert credit.remaining == original_remaining
        assert credit.total_purchased == original_purchased

    async def test_purchase_zero_messages_documents_no_service_validation(
        self, db: AsyncSession, test_credit
    ):
        """
        The service has NO validation for count=0 — that is the router's job.
        Calling purchase(db, user_id, 0) directly → debit(amount=0).

        ⚠️  This test DOCUMENTS current behavior, not desired behavior.
            If you want the service to guard against 0, add the check there
            and update this test to expect ValueError.
        """
        mock_tx = self._make_mock_wallet_tx()
        mock_debit = AsyncMock(return_value=mock_tx)

        with patch(
            "app.services.credit_service.WalletService.debit",
            new=mock_debit,
        ):
            result = await CreditService.purchase(db, test_credit.user_id, 0)

        mock_debit.assert_called_once()
        assert result["purchased"] == 0
        assert result["amount_charged"] == 0

    async def test_wallet_tx_id_in_result_matches_debit_return(
        self, db: AsyncSession, test_credit
    ):
        """
        The wallet_tx_id in the result dict must come from
        the WalletTransaction returned by debit(), not be generated fresh.
        """
        specific_tx_id = "specific-tx-id-abc-123"
        mock_tx = self._make_mock_wallet_tx(specific_tx_id)

        with patch(
            "app.services.credit_service.WalletService.debit",
            new=AsyncMock(return_value=mock_tx),
        ):
            result = await CreditService.purchase(db, test_credit.user_id, MIN_BUY)

        assert result["wallet_tx_id"] == specific_tx_id


# =============================================================================
# get_info
# =============================================================================

class TestGetInfo:

    async def test_returns_correct_keys(self, db: AsyncSession, test_credit):
        """get_info must return exactly these 3 keys — no more, no less."""
        info = await CreditService.get_info(db, test_credit.user_id)

        assert set(info.keys()) == {
            "remaining_messages",
            "total_purchased",
            "total_used",
        }

    async def test_values_match_db(self, db: AsyncSession, test_user):
        """Values in dict must match what is actually stored in the DB."""
        credit = MessageCredit(
            user_id=test_user.id,
            remaining=42,
            total_purchased=100,
            total_used=58,
            rejected_count=0,
        )
        db.add(credit)
        await db.flush()

        info = await CreditService.get_info(db, test_user.id)

        assert info["remaining_messages"] == 42
        assert info["total_purchased"] == 100
        assert info["total_used"] == 58

    async def test_new_user_gets_free_messages_as_remaining(
        self, db: AsyncSession, test_user
    ):
        """
        get_info calls get_or_create internally.
        Brand new user gets FREE_MESSAGES_FOR_NEW_USERS as remaining_messages.
        """
        info = await CreditService.get_info(db, test_user.id)

        assert info["remaining_messages"] == FREE
        assert info["total_purchased"] == 0
        assert info["total_used"] == 0

    async def test_get_info_reflects_updated_values_after_consume(
        self, db: AsyncSession, test_credit
    ):
        """
        get_info must return the CURRENT state after mutations.
        Consume 1 → get_info → remaining_messages reflects the change.
        """
        await CreditService.consume_one(db, test_credit.user_id)
        info = await CreditService.get_info(db, test_credit.user_id)

        assert info["remaining_messages"] == FREE - 1
        assert info["total_used"] == 1

    async def test_get_info_does_not_create_duplicate_on_existing(
        self, db: AsyncSession, test_credit
    ):
        """
        get_info calls get_or_create internally.
        Calling it on an existing user must not create a second row.
        """
        await CreditService.get_info(db, test_credit.user_id)
        await CreditService.get_info(db, test_credit.user_id)

        result = await db.execute(
            select(MessageCredit).where(
                MessageCredit.user_id == test_credit.user_id
            )
        )
        rows = result.scalars().all()
        assert len(rows) == 1

    async def test_get_info_after_purchase_shows_correct_remaining(
        self, db: AsyncSession, test_credit
    ):
        """
        Purchase increases remaining. get_info must reflect the post-purchase value.
        """
        mock_tx = MagicMock()
        mock_tx.id = "tx-for-info-test"

        with patch(
            "app.services.credit_service.WalletService.debit",
            new=AsyncMock(return_value=mock_tx),
        ):
            await CreditService.purchase(db, test_credit.user_id, MIN_BUY)

        info = await CreditService.get_info(db, test_credit.user_id)
        assert info["remaining_messages"] == FREE + MIN_BUY
        assert info["total_purchased"] == MIN_BUY
