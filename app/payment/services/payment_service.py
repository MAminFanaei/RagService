"""
Payment Service — Core Business Logic

Orchestrates the complete SEP payment flow:

INITIATION:
    1. Validate amount
    2. Apply discount (if provided)
    3. Generate unique ResNum
    4. Create payment record (PENDING)
    5. Request token from SEP
    6. Update payment (TOKEN_OBTAINED)
    7. Return token + redirect URL to frontend

CALLBACK PROCESSING:
    1. Find payment by ResNum
    2. Check callback State/Status from SEP
    3. Double-spend check (3 layers)
    4. Acquire Redis lock on RefNum
    5. Call VerifyTransaction on SEP
    6. Handle verify result:
       - ResultCode 0: First successful verify
       - ResultCode 2: Duplicate verify (SEP confirms again)
       - Negative: Verify failed
    7. Amount check (3 cases from SEP docs):
       - Case A: Amounts match → credit wallet
       - Case B: Amounts don't match → auto-reverse
       - Case C: Error response → mark failed
    8. Credit wallet + record discount usage
    9. Return result
"""

import uuid
import time
import structlog
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
import redis.asyncio as aioredis

from app.payment.config import payment_settings
from app.payment.core.constants import (
    PaymentStatus,
    SEPResultCode,
    RES_NUM_PREFIX,
)
from app.payment.core.locker import acquire_lock
from app.payment.core.metrics import metrics
from app.payment.models.payment import Payment
from app.payment.services.sep_client import sep_client, CallbackData
from app.payment.services.wallet_service import WalletService
from app.payment.services.discount_service import DiscountService
from app.payment.services.double_spend_guard import DoubleSpendGuard
from app.payment.exceptions import (
    InvalidAmountException,
    PaymentNotFoundException,
    SEPTokenException,
    SEPTimeoutException,
    DoubleSpendException,
    LockAcquisitionException,
)

logger = structlog.get_logger()


class PaymentService:
    """
    Core payment operations.
    All methods are static — no instance state.
    """

    @staticmethod
    def _generate_res_num() -> str:
        timestamp = int(time.time() * 1000)
        short_uuid = uuid.uuid4().hex[:8]
        return f"{RES_NUM_PREFIX}-{timestamp}-{short_uuid}"

    @staticmethod
    async def initiate_payment(
        db: AsyncSession,
        user_id: str,
        amount: int,
        description: str = "",
        discount_code: Optional[str] = None,
        cell_number: Optional[str] = None,
    ) -> dict:
        # Validate amount
        if amount < payment_settings.MIN_PAYMENT_AMOUNT or amount > payment_settings.MAX_PAYMENT_AMOUNT:
            raise InvalidAmountException(
                amount=amount,
                min_amount=payment_settings.MIN_PAYMENT_AMOUNT,
                max_amount=payment_settings.MAX_PAYMENT_AMOUNT,
            )

        original_amount = amount
        discount_code_id = None
        discount_amount = 0

        if discount_code:
            discount_result = await DiscountService.validate_and_calculate(
                db=db, code=discount_code, user_id=user_id, amount=amount,
            )
            discount_amount = discount_result["discount_amount"]
            discount_code_id = discount_result["discount_code_id"]
            amount = discount_result["final_amount"]

        res_num = PaymentService._generate_res_num()

        payment = Payment(
            id=str(uuid.uuid4()),
            user_id=user_id,
            res_num=res_num,
            amount=amount,
            original_amount=original_amount,
            discount_code_id=discount_code_id,
            discount_amount=discount_amount,
            terminal_id=payment_settings.SEP_TERMINAL_ID,
            status=PaymentStatus.PENDING,
            description=description,
        )
        db.add(payment)
        await db.flush()

        logger.info("payment_initiating", payment_id=payment.id, user_id=user_id,
                     amount=amount, original_amount=original_amount, res_num=res_num)

        token_response = await sep_client.request_token(
            amount=amount, res_num=res_num,
            redirect_url=payment_settings.PAYMENT_CALLBACK_URL,
            cell_number=cell_number,
        )

        if not token_response.success:
            payment.status = PaymentStatus.FAILED
            payment.failure_reason = f"Token error: [{token_response.error_code}] {token_response.error_desc}"
            await db.commit()
            logger.warning("payment_token_failed", payment_id=payment.id,
                          error_code=token_response.error_code, error_desc=token_response.error_desc)
            raise SEPTokenException(sep_error_code=token_response.error_code,
                                   sep_error_desc=token_response.error_desc)

        payment.token = token_response.token
        payment.status = PaymentStatus.TOKEN_OBTAINED
        await db.commit()

        redirect_url = sep_client.build_redirect_url(token_response.token)
        metrics.payment_initiated()

        logger.info("payment_initiated", payment_id=payment.id, res_num=res_num,
                     amount=amount, has_discount=bool(discount_code))

        return {
            "payment_id": payment.id,
            "res_num": res_num,
            "token": token_response.token,
            "redirect_url": redirect_url,
            "amount": amount,
            "original_amount": original_amount,
            "discount_amount": discount_amount,
        }

    @staticmethod
    async def process_callback(
        db: AsyncSession,
        redis_client: aioredis.Redis,
        callback_data: CallbackData,
    ) -> dict:
        start_time = time.time()

        logger.info("callback_received", res_num=callback_data.res_num,
                     state=callback_data.state, status=callback_data.status,
                     ref_num=(callback_data.ref_num[:20] + "..."
                              if callback_data.ref_num and len(callback_data.ref_num) > 20
                              else callback_data.ref_num),
                     amount=callback_data.amount)

        # Step 1: Find payment by ResNum
        result = await db.execute(select(Payment).where(Payment.res_num == callback_data.res_num))
        payment = result.scalar_one_or_none()

        if not payment:
            logger.error("callback_unknown_resnum", res_num=callback_data.res_num)
            raise PaymentNotFoundException(res_num=callback_data.res_num)

        payment.callback_at = datetime.now(timezone.utc)
        payment.state = callback_data.state
        payment.status_code = callback_data.status
        payment.rrn = callback_data.rrn
        payment.trace_no = callback_data.trace_no
        payment.secure_pan = callback_data.secure_pan
        payment.hashed_card_number = callback_data.hashed_card_number

        # Step 2: Check callback status
        if not callback_data.is_ok:
            payment.status = PaymentStatus.FAILED
            payment.failure_reason = f"Callback: {callback_data.state} (Status={callback_data.status})"
            await db.commit()
            logger.info("callback_payment_not_ok", payment_id=payment.id,
                        state=callback_data.state, status=callback_data.status)
            return {"payment_id": payment.id, "status": "FAILED",
                    "reason": callback_data.status_description}

        # Step 3: Check RefNum exists
        if not callback_data.has_ref_num:
            payment.status = PaymentStatus.FAILED
            payment.failure_reason = "Empty RefNum received from SEP"
            await db.commit()
            logger.error("callback_empty_refnum", payment_id=payment.id)
            return {"payment_id": payment.id, "status": "FAILED",
                    "reason": "Empty digital receipt from payment gateway"}

        # Step 4: Double-spend prevention (Layer 3)
        guard_result = await DoubleSpendGuard.check_ref_num_for_callback(
            db=db, ref_num=callback_data.ref_num, current_payment_id=payment.id)

        if guard_result["is_duplicate"]:
            existing = guard_result["existing_payment"]
            if existing and existing.id == payment.id:
                return {"payment_id": existing.id, "status": "VERIFIED",
                        "amount": existing.amount, "ref_num": existing.ref_num,
                        "message": "Payment was already verified"}

            payment.status = PaymentStatus.FAILED
            payment.failure_reason = f"Double spend blocked: {guard_result['reason']}"
            await db.commit()
            metrics.double_spend_blocked()
            logger.critical("double_spend_blocked", payment_id=payment.id,
                           ref_num=callback_data.ref_num, reason=guard_result["reason"])
            raise DoubleSpendException(ref_num=callback_data.ref_num)

        payment.ref_num = callback_data.ref_num
        payment.status = PaymentStatus.CALLBACK_RECEIVED
        await db.flush()

        # Step 5: Acquire Redis lock (Layer 2)
        lock_key = f"callback:{callback_data.ref_num}"

        try:
            async with acquire_lock(redis_client, lock_key):
                # Step 6: Call VerifyTransaction
                try:
                    verify_response = await sep_client.verify_transaction(
                        ref_num=callback_data.ref_num)
                except SEPTimeoutException:
                    payment.status = PaymentStatus.VERIFY_TIMEOUT
                    payment.failure_reason = ("All verify retries timed out. "
                        "Transaction will be auto-reversed by SEP in 30 minutes.")
                    await db.commit()
                    logger.error("verify_all_retries_timeout", payment_id=payment.id,
                                ref_num=callback_data.ref_num)
                    return {"payment_id": payment.id, "status": "VERIFY_TIMEOUT",
                            "reason": ("Payment verification timed out. If money was deducted, "
                                       "it will be automatically refunded within 30 minutes.")}

                payment.sep_result_code = verify_response.result_code
                payment.sep_result_description = verify_response.result_description
                if verify_response.transaction_detail:
                    payment.verified_amount = verify_response.transaction_detail.original_amount

                # Step 7: Handle verify result
                if verify_response.result_code in (SEPResultCode.SUCCESS, SEPResultCode.DUPLICATE_REQUEST):
                    verified_amount = verify_response.verified_amount

                    if verified_amount is None:
                        payment.status = PaymentStatus.FAILED
                        payment.failure_reason = "Verify response missing amount"
                        await db.commit()
                        return {"payment_id": payment.id, "status": "FAILED",
                                "reason": "Verification error: missing amount"}

                    # Step 8: Amount check
                    if verified_amount == payment.amount:
                        # CASE A: SUCCESS
                        payment.status = PaymentStatus.VERIFIED
                        payment.verified_at = datetime.now(timezone.utc)

                        await WalletService.credit(
                            db=db, user_id=payment.user_id, amount=payment.amount,
                            payment_id=payment.id,
                            description=f"Wallet charge via SEP - {payment.res_num}")

                        if payment.discount_code_id:
                            await DiscountService.record_usage(
                                db=db, discount_code_id=payment.discount_code_id,
                                user_id=payment.user_id, payment_id=payment.id,
                                discount_amount=payment.discount_amount)

                        await db.commit()
                        duration = time.time() - start_time
                        metrics.payment_verified.labels(terminal_id=payment.terminal_id or "default").inc()
                        if duration is not None:
                            metrics._payment_duration.observe(duration)
                        logger.info("payment_verified_success", payment_id=payment.id,
                                    amount=payment.amount, ref_num=payment.ref_num,
                                    duration=round(duration, 3))

                        return {
                            "payment_id": payment.id, "status": "VERIFIED",
                            "amount": payment.amount, "original_amount": payment.original_amount,
                            "discount_amount": payment.discount_amount, "ref_num": payment.ref_num,
                            "rrn": payment.rrn, "trace_no": payment.trace_no,
                            "secure_pan": payment.secure_pan,
                            "verified_at": (payment.verified_at.isoformat()
                                           if payment.verified_at else None),
                        }
                    else:
                        # CASE B: AMOUNT MISMATCH — auto-reverse
                        payment.status = PaymentStatus.AMOUNT_MISMATCH
                        payment.failure_reason = (f"Amount mismatch: expected {payment.amount}, "
                                                  f"verified {verified_amount}")
                        try:
                            reverse_result = await sep_client.reverse_transaction(
                                ref_num=callback_data.ref_num)
                            payment.failure_reason += f" | Auto-reversed: code={reverse_result.result_code}"
                        except Exception as e:
                            payment.failure_reason += f" | Auto-reverse failed: {str(e)}"

                        await db.commit()
                        logger.error("payment_amount_mismatch", payment_id=payment.id,
                                    expected=payment.amount, verified=verified_amount)
                        return {"payment_id": payment.id, "status": "AMOUNT_MISMATCH",
                                "reason": (f"Expected {payment.amount:,} Rials, "
                                           f"verified {verified_amount:,} Rials. Auto-reverse initiated.")}

                else:
                    # CASE C: VERIFY ERROR
                    payment.status = PaymentStatus.FAILED
                    payment.failure_reason = (f"Verify error: [{verify_response.result_code}] "
                                              f"{verify_response.result_description}")
                    await db.commit()
                    metrics.payment_failed(f"verify_{verify_response.result_code}")
                    logger.warning("payment_verify_failed", payment_id=payment.id,
                                  result_code=verify_response.result_code,
                                  result_description=verify_response.result_description)
                    return {"payment_id": payment.id, "status": "FAILED",
                            "reason": (verify_response.result_description
                                       or f"Verify error code: {verify_response.result_code}")}

        except LockAcquisitionException:
            logger.warning("callback_lock_contention", payment_id=payment.id,
                          ref_num=callback_data.ref_num)
            raise

    @staticmethod
    async def get_payment(db: AsyncSession, payment_id: str,
                          user_id: Optional[str] = None) -> Payment:
        query = select(Payment).where(Payment.id == payment_id)
        if user_id:
            query = query.where(Payment.user_id == user_id)
        result = await db.execute(query)
        payment = result.scalar_one_or_none()
        if not payment:
            raise PaymentNotFoundException(payment_id=payment_id)
        return payment

    @staticmethod
    async def list_payments(db: AsyncSession, user_id: str,
                            status: Optional[str] = None,
                            limit: int = 50, offset: int = 0) -> dict:
        limit = min(limit, 100)
        query = select(Payment).where(Payment.user_id == user_id)
        if status:
            query = query.where(Payment.status == status)
        count_query = select(func.count(Payment.id)).where(Payment.user_id == user_id)
        if status:
            count_query = count_query.where(Payment.status == status)
        total_result = await db.execute(count_query)
        total = total_result.scalar()
        query = query.order_by(Payment.created_at.desc()).limit(limit).offset(offset)
        result = await db.execute(query)
        payments = result.scalars().all()
        return {"total": total, "limit": limit, "offset": offset, "payments": payments}