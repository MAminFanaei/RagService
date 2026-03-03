"""
Double-Spend Prevention Guard

SEP Documentation states (Section 5, نکته الف):
    "مصرف شدن رسید دیجیتالی در سمت فروشنده تعیین و نگهداری می‌شود و نه 
    در سمت پرداخت الکترونیک سامان"
    
    Translation: "Tracking whether a digital receipt (RefNum) has been consumed 
    is determined and maintained on the merchant's side, NOT on SEP's side."

And critically:
    "اگر یک رسید دیجیتالی جهت تایید بیش از یک بار به پرداخت الکترونیک 
    سامان ارائه شود، پرداخت الکترونیک سامان مجددا آن را تایید می‌کند"
    
    Translation: "If a digital receipt is submitted for verification more than 
    once, SEP will verify it again."

This means SEP will happily verify the same RefNum multiple times. 
We MUST prevent crediting the wallet more than once per RefNum.

3-Layer Protection:
    Layer 1: DATABASE — ref_num column has UNIQUE constraint on payments table.
             INSERT fails if same RefNum already exists.
    
    Layer 2: REDIS LOCK — Before processing any callback, acquire a distributed
             lock on the RefNum. If another worker is already processing the
             same RefNum, we wait or fail. This prevents race conditions in
             multi-worker deployments.
    
    Layer 3: APPLICATION CHECK — Before calling VerifyTransaction, query the DB
             to see if this RefNum is already VERIFIED. If yes, return the
             existing result without calling Verify again or crediting wallet.
"""

import structlog
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.payment.models.payment import Payment
from app.payment.core.constants import PaymentStatus

logger = structlog.get_logger()


class DoubleSpendGuard:
    """
    Prevents double-spending of SEP RefNums (digital receipts).
    
    Usage:
        # Layer 3: Application-level check before verify
        existing = await DoubleSpendGuard.check_and_get_verified(db, ref_num)
        if existing:
            return existing  # Already processed, don't verify again
        
        # Layer 1: DB unique constraint handles concurrent inserts
        # Layer 2: Redis lock handles concurrent callback processing
        # (These are handled by the caller — PaymentService.process_callback)
    """

    @staticmethod
    async def check_ref_num_exists(
        db: AsyncSession,
        ref_num: str,
        exclude_payment_id: Optional[str] = None,
    ) -> bool:
        """
        Check if a RefNum already exists in our database.
        
        This is Layer 1 (application-level check before DB insert).
        The actual UNIQUE constraint on the column is the real Layer 1,
        but this check prevents unnecessary INSERT attempts.
        
        Args:
            db: Database session.
            ref_num: The RefNum to check.
            exclude_payment_id: Optionally exclude a specific payment
                               (useful when updating our own payment record).
        
        Returns:
            True if RefNum already exists (potential double-spend).
        """
        query = select(Payment.id).where(Payment.ref_num == ref_num)
        
        if exclude_payment_id:
            query = query.where(Payment.id != exclude_payment_id)
        
        result = await db.execute(query)
        existing = result.scalar_one_or_none()
        
        if existing:
            logger.warning(
                "double_spend_ref_num_exists",
                ref_num=ref_num[:20] + "..." if len(ref_num) > 20 else ref_num,
                existing_payment_id=existing,
                exclude_payment_id=exclude_payment_id,
            )
            return True
        
        return False

    @staticmethod
    async def get_verified_payment_by_ref_num(
        db: AsyncSession,
        ref_num: str,
    ) -> Optional[Payment]:
        """
        Check if a RefNum has already been verified and wallet credited.
        
        This is the critical Layer 3 check. If a payment with this RefNum
        is already in VERIFIED status, we know:
        1. Verify was called successfully
        2. Wallet was already credited
        3. We must NOT credit again
        
        Args:
            db: Database session.
            ref_num: The RefNum to check.
        
        Returns:
            The existing verified Payment if found, None otherwise.
        """
        query = select(Payment).where(
            Payment.ref_num == ref_num,
            Payment.status == PaymentStatus.VERIFIED,
        )
        
        result = await db.execute(query)
        existing = result.scalar_one_or_none()
        
        if existing:
            logger.warning(
                "double_spend_already_verified",
                ref_num=ref_num[:20] + "...",
                existing_payment_id=existing.id,
                verified_at=str(existing.verified_at),
            )
        
        return existing

    @staticmethod
    async def check_ref_num_for_callback(
        db: AsyncSession,
        ref_num: str,
        current_payment_id: str,
    ) -> dict:
        """
        Full double-spend check for callback processing.
        
        Combines all application-level checks into one method.
        Called after receiving callback but before calling Verify.
        
        Args:
            db: Database session.
            ref_num: The RefNum from SEP callback.
            current_payment_id: The payment we're currently processing.
        
        Returns:
            dict with:
                - is_duplicate: bool — True if this is a double-spend attempt
                - existing_payment: Optional[Payment] — The already-verified payment
                - reason: str — Human-readable explanation
        """
        # Check 1: Is this RefNum already verified by another payment?
        verified = await DoubleSpendGuard.get_verified_payment_by_ref_num(
            db, ref_num
        )
        if verified and verified.id != current_payment_id:
            logger.critical(
                "double_spend_different_payment",
                ref_num=ref_num[:20] + "...",
                current_payment_id=current_payment_id,
                existing_payment_id=verified.id,
            )
            return {
                "is_duplicate": True,
                "existing_payment": verified,
                "reason": f"RefNum already verified by payment {verified.id}",
            }
        
        # Check 2: Is this RefNum used by another payment (any status)?
        exists = await DoubleSpendGuard.check_ref_num_exists(
            db, ref_num, exclude_payment_id=current_payment_id
        )
        if exists:
            return {
                "is_duplicate": True,
                "existing_payment": None,
                "reason": f"RefNum already assigned to another payment",
            }
        
        # Check 3: Is our own payment already verified?
        # This handles the case where SEP sends the callback twice
        query = select(Payment).where(
            Payment.id == current_payment_id,
            Payment.status == PaymentStatus.VERIFIED,
        )
        result = await db.execute(query)
        self_verified = result.scalar_one_or_none()
        
        if self_verified:
            logger.info(
                "double_spend_self_already_verified",
                payment_id=current_payment_id,
                ref_num=ref_num[:20] + "...",
            )
            return {
                "is_duplicate": True,
                "existing_payment": self_verified,
                "reason": "This payment is already verified",
            }
        
        return {
            "is_duplicate": False,
            "existing_payment": None,
            "reason": "",
        }
