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
        await db.flush()

        logger.info(
            "credit_record_created",
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
        result = await db.execute(
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

        if result.rowcount == 0:
            logger.warning("consume_one_failed_no_credits", user_id=user_id)
            return 0

        # Re-read to get actual value
        credit = await CreditService._get(db, user_id)
        return credit.remaining if credit else 0

    @staticmethod
    async def record_rejection(db: AsyncSession, user_id: str) -> dict:
        """
        Record an off-topic rejection.

        rejected_count is a LIFETIME counter — it never resets.
        First MAX_FREE_REJECTIONS rejections are free.
        Every rejection after that costs 1 credit, forever.

        Returns dict with charged (bool), free_rejections_remaining, credits_remaining.
        """
        credit = await CreditService.get_or_create(db, user_id)

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
                    rejected_count=MessageCredit.rejected_count + 1,  # keeps counting up
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await db.flush()
            return {
                "charged": True,
                "free_rejections_remaining": 0,          # lifetime quota gone, always 0
                "credits_remaining": max(credit.remaining - 1, 0),
            }
        else:
            new_count = credit.rejected_count + 1        # compute BEFORE using in return

            await db.execute(
                update(MessageCredit)
                .where(MessageCredit.user_id == user_id)
                .values(
                    rejected_count=new_count,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await db.flush()
            return {
                "charged": False,
                "free_rejections_remaining": settings.MAX_FREE_REJECTIONS - new_count,
                "credits_remaining": credit.remaining,
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

        # to ensure the row exists
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

        # Re-read AFTER the UPDATE to get the actual committed value
        credit = await CreditService._get(db, user_id)
        new_remaining = credit.remaining

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
        result = await db.execute(
            select(MessageCredit).where(MessageCredit.user_id == user_id)
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
        credit = await CreditService.get_or_create(db, user_id)

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

        # Re-read after UPDATE — same pattern as purchase()
        refreshed = await CreditService._get(db, user_id)

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