"""
Reverse Service — Full Transaction Reversal

Handles reversing a verified payment within SEP's time window.

Per SEP docs (Section 10):
    "در صورتی که پذیرنده تراکنش تایید (verify) کرده باشد، می‌تواند تا 
    50 دقیقه پس از زمان انجام تراکنش، سرویس Reverse را جهت ثبت درخواست 
    بازگشت وجه به حساب صاحب کارت فراخوانی کند."
    
    Translation: "If the merchant has verified a transaction, they can call 
    the Reverse service within 50 minutes of the transaction time to request 
    a refund to the cardholder's account."

Important constraints:
    - Only VERIFIED payments can be reversed
    - Must be within 50 minutes of original transaction
    - Full amount is reversed (no partial reversal)
    - We debit the wallet before calling SEP (then rollback if SEP fails)
"""

import uuid
import structlog
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
import redis.asyncio as aioredis

from app.payment.config import payment_settings
from app.payment.core.constants import (
    PaymentStatus,
    ReverseStatus,
    SEPResultCode,
)
from app.payment.core.locker import acquire_lock, reverse_lock_key
from app.payment.core.metrics import metrics
from app.payment.models.payment import Payment
from app.payment.models.reverse import Reverse
from app.payment.services.sep_client import sep_client
from app.payment.services.wallet_service import WalletService
from app.payment.exceptions import (
    PaymentNotFoundException,
    PaymentNotReversibleException,
    ReverseWindowExpiredException,
    SEPReverseException,
    LockAcquisitionException,
)

logger = structlog.get_logger()


class ReverseService:
    """
    Transaction reversal operations.
    
    Only supports full reversal (no partial refunds per SEP docs).
    """

    @staticmethod
    async def reverse_payment(
        db: AsyncSession,
        redis_client: aioredis.Redis,
        payment_id: str,
        user_id: str,
        reason: str = "customer_request",
    ) -> dict:
        """
        Reverse a verified payment.
        
        Flow:
        1. Validate: payment exists, is VERIFIED, within time window
        2. Acquire Redis lock on payment
        3. Create Reverse record (PENDING)
        4. Call SEP ReverseTransaction API
        5. On success: debit wallet, mark payment REVERSED
        6. On failure: mark reverse FAILED, payment stays VERIFIED
        
        Args:
            db: Database session.
            redis_client: Redis client for distributed locking.
            payment_id: UUID of the payment to reverse.
            user_id: User requesting the reverse (must own the payment).
            reason: Reason for reversal.
        
        Returns:
            dict with reverse_id, status, and details.
        """
        # Step 1: Get and validate payment
        result = await db.execute(
            select(Payment).where(
                Payment.id == payment_id,
                Payment.user_id == user_id,
            )
        )
        payment = result.scalar_one_or_none()

        if not payment:
            raise PaymentNotFoundException(payment_id=payment_id)

        if payment.status != PaymentStatus.VERIFIED:
            raise PaymentNotReversibleException(
                current_status=payment.status,
            )

        if not payment.ref_num:
            raise PaymentNotReversibleException(
                message="Payment has no RefNum — cannot reverse",
            )

        # Check time window (50 minutes)
        now = datetime.now(timezone.utc)
        if payment.verified_at:
            verified_at = payment.verified_at
            # SQLite returns naive datetimes — make them aware
            if verified_at.tzinfo is None:
                verified_at = verified_at.replace(tzinfo=timezone.utc)
            elapsed = (now - verified_at).total_seconds()
            elapsed_minutes = int(elapsed / 60)

            if elapsed_minutes > payment_settings.PAYMENT_REVERSE_WINDOW_MINUTES:
                raise ReverseWindowExpiredException(
                    minutes_elapsed=elapsed_minutes,
                    window_minutes=payment_settings.PAYMENT_REVERSE_WINDOW_MINUTES,
                )

        logger.info(
            "reverse_initiating",
            payment_id=payment_id,
            amount=payment.amount,
            ref_num=payment.ref_num[:20] + "...",
            reason=reason,
        )

        # Step 2: Acquire lock
        lock_key = reverse_lock_key(payment.id)

        try:
            async with acquire_lock(redis_client, lock_key):

                # Step 3: Create reverse record
                reverse = Reverse(
                    id=str(uuid.uuid4()),
                    payment_id=payment.id,
                    ref_num=payment.ref_num,
                    amount=payment.amount,
                    reason=reason,
                    status=ReverseStatus.PENDING,
                )
                db.add(reverse)
                await db.flush()

                # Step 4: Call SEP Reverse API
                try:
                    reverse_response = await sep_client.reverse_transaction(
                        ref_num=payment.ref_num,
                    )
                except Exception as e:
                    # SEP call failed
                    reverse.status = ReverseStatus.FAILED
                    reverse.result_description = str(e)
                    await db.commit()

                    logger.error(
                        "reverse_sep_call_failed",
                        payment_id=payment_id,
                        reverse_id=reverse.id,
                        error=str(e),
                    )

                    raise SEPReverseException(
                        message=f"SEP reverse failed: {str(e)}",
                    )

                # Store SEP response
                reverse.result_code = reverse_response.result_code
                reverse.result_description = (
                    reverse_response.result_description
                )

                # Step 5: Check result
                if reverse_response.result_code in (
                    SEPResultCode.SUCCESS,
                    SEPResultCode.DUPLICATE_REQUEST,
                ):
                    # Reverse succeeded
                    reverse.status = ReverseStatus.COMPLETED

                    # Debit wallet (return the credited amount)
                    try:
                        await WalletService.debit(
                            db=db,
                            user_id=payment.user_id,
                            amount=payment.amount,
                            payment_id=payment.id,
                            description=(
                                f"Reverse - {payment.res_num}"
                            ),
                        )
                    except Exception as wallet_err:
                        # Wallet debit failed but SEP reverse succeeded
                        # This is a critical inconsistency — log it
                        logger.critical(
                            "reverse_wallet_debit_failed",
                            payment_id=payment_id,
                            reverse_id=reverse.id,
                            error=str(wallet_err),
                            amount=payment.amount,
                        )
                        reverse.result_description = (
                            f"{reverse.result_description} "
                            f"| Wallet debit failed: {str(wallet_err)}"
                        )

                    # Mark payment as reversed
                    payment.status = PaymentStatus.REVERSED
                    await db.commit()

                    metrics.payment_reversed(amount=payment.amount)

                    logger.info(
                        "reverse_completed",
                        payment_id=payment_id,
                        reverse_id=reverse.id,
                        amount=payment.amount,
                    )

                    return {
                        "reverse_id": reverse.id,
                        "payment_id": payment.id,
                        "status": "COMPLETED",
                        "amount": payment.amount,
                        "ref_num": payment.ref_num,
                    }
                else:
                    # Reverse failed
                    reverse.status = ReverseStatus.FAILED
                    await db.commit()

                    metrics.reverse_failed_metric()

                    logger.warning(
                        "reverse_failed",
                        payment_id=payment_id,
                        reverse_id=reverse.id,
                        result_code=reverse_response.result_code,
                        result_desc=reverse_response.result_description,
                    )

                    return {
                        "reverse_id": reverse.id,
                        "payment_id": payment.id,
                        "status": "FAILED",
                        "reason": (
                            reverse_response.result_description
                            or f"SEP error: {reverse_response.result_code}"
                        ),
                    }

        except LockAcquisitionException:
            logger.warning(
                "reverse_lock_contention",
                payment_id=payment_id,
            )
            raise

    @staticmethod
    async def list_reverses(
        db: AsyncSession,
        payment_id: str,
        user_id: Optional[str] = None,
    ) -> dict:
        """
        List all reverse attempts for a payment.
        
        Args:
            db: Database session.
            payment_id: Payment to list reverses for.
            user_id: If provided, verify payment ownership.
        
        Returns:
            dict with payment_id and list of reverses.
        """
        # Verify payment exists (and optionally owned by user)
        pay_query = select(Payment).where(Payment.id == payment_id)
        if user_id:
            pay_query = pay_query.where(Payment.user_id == user_id)

        pay_result = await db.execute(pay_query)
        payment = pay_result.scalar_one_or_none()

        if not payment:
            raise PaymentNotFoundException(payment_id=payment_id)

        # Get reverses
        query = (
            select(Reverse)
            .where(Reverse.payment_id == payment_id)
            .order_by(Reverse.created_at.desc())
        )

        result = await db.execute(query)
        reverses = result.scalars().all()

        return {
            "payment_id": payment_id,
            "payment_status": payment.status,
            "total": len(reverses),
            "reverses": reverses,
        }
