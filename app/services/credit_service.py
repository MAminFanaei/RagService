"""
Credit Service

Manages message credits: get/create, consume on success,
handle rejections, purchase with wallet debit.
All methods use flush() — caller (router) must commit().
"""

import structlog
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.models.credit import MessageCredit
from app.payment.services.wallet_service import WalletService
from app.config import settings

logger = structlog.get_logger()


class CreditService:

    @staticmethod
    async def get_or_create(db: AsyncSession, user_id: str) -> MessageCredit:
        """
        Get existing credit record or create with free messages.
        Uses flush() only — caller must commit().
        """
        result = await db.execute(
            select(MessageCredit).where(MessageCredit.user_id == user_id)
        )
        credit = result.scalar_one_or_none()

        if credit:
            return credit

        credit = MessageCredit(
            user_id=user_id,
            remaining=settings.FREE_MESSAGES_FOR_NEW_USERS,
            total_purchased=0,
            total_used=0,
            rejected_count=0,
        )
        db.add(credit)
        await db.flush()  # caller owns the commit

        logger.info(
            "credit_record_not_found_and_created",
            user_id=user_id,
            free_messages=settings.FREE_MESSAGES_FOR_NEW_USERS,
        )
        return credit

    @staticmethod
    async def consume_one(db: AsyncSession, user_id: str) -> int:
        """
        Consume 1 credit after successful response.
        Resets rejected_count to 0.
        Returns new remaining count.
        Atomic — uses SQL-level decrement with remaining > 0 guard.
        """
        await db.execute(
            update(MessageCredit)
            .where(
                MessageCredit.user_id == user_id,
                MessageCredit.remaining > 0,
            )
            .values(
                remaining=MessageCredit.remaining - 1,
                total_used=MessageCredit.total_used + 1,
                rejected_count=0,
                updated_at=datetime.now(timezone.utc),
            )
        )

        await db.flush()

        # Always re-read fresh from DB — never trust identity map after
        # add_message()'s internal commits have dirtied the session cache.
        credit = await CreditService._get_fresh(db, user_id)
        if credit is None:
            logger.warning("consume_one_credit_row_missing", user_id=user_id)
            return 0
        
        logger.info(
                "credit_consumed",
                user_id=user_id,
                remaining=credit.remaining,
            )
        return credit.remaining

    @staticmethod
    async def record_rejection(db: AsyncSession, user_id: str) -> dict:
        """
        Record an off-topic rejection.

        rejected_count is a LIFETIME counter — it never resets.
        First MAX_FREE_REJECTIONS rejections are free.
        Every rejection after that costs 1 credit, forever.

        Returns dict with charged (bool), free_rejections_remaining, credits_remaining.
        """
        # Always read fresh — this runs after add_message() commits, so the
        # identity map may hold a stale object.
        credit = await CreditService._get_fresh(db, user_id)
        if credit is None:
            # Should never happen (get_or_create runs before RAG), but be safe.
            logger.warning("record_rejection_credit_row_missing", user_id=user_id)
            return {"charged": False, "free_rejections_remaining": 0, "credits_remaining": 0}

        if credit.rejected_count >= settings.MAX_FREE_REJECTIONS:
            # Lifetime free quota exhausted — charge 1 credit, counter keeps climbing
            await db.execute(
                update(MessageCredit)
                .where(
                    MessageCredit.user_id == user_id,
                    MessageCredit.remaining > 0,
                )
                .values(
                    remaining=MessageCredit.remaining - 1,
                    total_used=MessageCredit.total_used + 1,
                    rejected_count=MessageCredit.rejected_count + 1,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await db.flush()
            
            # Re-read to get the real post-UPDATE value
            updated = await CreditService._get_fresh(db, user_id)
            logger.info(
                "rejection_charged_no_free_quota_left",
                user_id=user_id,
                credits_remaining=updated.remaining if updated else 0,
            )
            
            return {
                "charged": True,
                "free_rejections_remaining": 0,
                "credits_remaining": updated.remaining if updated else 0,
            }
        else:
            new_count = credit.rejected_count + 1

            await db.execute(
                update(MessageCredit)
                .where(MessageCredit.user_id == user_id)
                .values(
                    rejected_count=new_count,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await db.flush()
            
            logger.info(
                "rejection_free_quota_available",
                user_id=user_id,
                rejected_count=new_count,
                free_rejections_remaining=settings.MAX_FREE_REJECTIONS - new_count,
                credits_remaining=credit.remaining,
            )
            
            return {
                "charged": False,
                "free_rejections_remaining": settings.MAX_FREE_REJECTIONS - new_count,
                "credits_remaining": credit.remaining,  # unchanged, no need to re-read
            }

    @staticmethod
    async def purchase(
        db: AsyncSession,
        user_id: str,
        message_count: int,
    ) -> dict:
        total_price = message_count * settings.PRICE_PER_MESSAGE

        wallet_tx = await WalletService.debit(
            db=db,
            user_id=user_id,
            amount=total_price,
            description=f"Purchased {message_count} messages",
        )

        # ensure the row exists
        await CreditService.get_or_create(db, user_id)

        await db.execute(
            update(MessageCredit)
            .where(MessageCredit.user_id == user_id)
            .values(
                remaining=MessageCredit.remaining + message_count,
                total_purchased=MessageCredit.total_purchased + message_count,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await db.flush()

        credit = await CreditService._get_fresh(db, user_id)
        new_remaining = credit.remaining if credit else 0

        logger.info(
            "credits_purchased",
            user_id=user_id,
            message_count=message_count,
            total_price=total_price,
            new_remaining=new_remaining,
        )

        return {
            "purchased": message_count,
            "amount_charged": total_price,
            "remaining": new_remaining,
            "wallet_tx_id": wallet_tx.id,
        }

    @staticmethod
    async def get_info(db: AsyncSession, user_id: str) -> dict:
        """Get credit info dict for /me endpoint."""
        credit = await CreditService.get_or_create(db, user_id)
        return {
            "remaining_messages": credit.remaining,
            "total_purchased": credit.total_purchased,
            "total_used": credit.total_used,
        }

    @staticmethod
    async def _get(db: AsyncSession, user_id: str) -> MessageCredit | None:
        """Standard get — may return cached identity-map object. Use only
        when you have not gone through any intermediate commits in this session."""
        result = await db.execute(
            select(MessageCredit).where(MessageCredit.user_id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def _get_fresh(db: AsyncSession, user_id: str) -> MessageCredit | None:
        """
        Always hits the DB and overwrites any stale identity-map entry.
        Use this after any UPDATE + flush, especially when the session has
        gone through intermediate commits (e.g. add_message() commits).
        """
        result = await db.execute(
            select(MessageCredit)
            .where(MessageCredit.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def admin_add_credits(
        db: AsyncSession,
        user_id: str,
        amount: int,
        reason: str,
        admin_id: str,
    ) -> dict:
        """
        Admin manually adds message credits to a user account.
        Does NOT debit wallet — this is a free grant.
        Caller must commit().
        """
        await CreditService.get_or_create(db, user_id)

        await db.execute(
            update(MessageCredit)
            .where(MessageCredit.user_id == user_id)
            .values(
                remaining=MessageCredit.remaining + amount,
                total_purchased=MessageCredit.total_purchased + amount,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await db.flush()

        refreshed = await CreditService._get_fresh(db, user_id)

        logger.info(
            "admin_credits_added",
            admin_id=admin_id,
            user_id=user_id,
            amount=amount,
            reason=reason,
            new_remaining=refreshed.remaining,
        )

        return {
            "credits_added": amount,
            "new_remaining": refreshed.remaining,
            "total_purchased": refreshed.total_purchased,
        }

    @staticmethod
    async def admin_add_wallet_balance(
        db: AsyncSession,
        user_id: str,
        amount: int,
        reason: str,
        admin_id: str,
    ) -> dict:
        """
        Admin manually adds money to a user wallet via WalletService.credit().
        Creates wallet if user has none (lazy init — same as everywhere else).
        Caller must commit().
        """
        tx = await WalletService.credit(
            db=db,
            user_id=user_id,
            amount=amount,
            description=f"[ADMIN GRANT] {reason} (by admin {admin_id})",
        )

        logger.info(
            "admin_wallet_topped_up",
            admin_id=admin_id,
            user_id=user_id,
            amount=amount,
            reason=reason,
            wallet_tx_id=tx.id,
            new_balance=tx.balance_after,
        )

        return {
            "amount_added": amount,
            "new_balance": tx.balance_after,
            "wallet_id": tx.wallet_id,
            "wallet_tx_id": tx.id,
        }
