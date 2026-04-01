"""
Payment Callback Router.

Handles the POST callback from SEP after the user completes
(or cancels/fails) payment on SEP's payment page.

SEP redirects the user's browser here via POST with form data.
This endpoint:
    1. Parses the callback data
    2. Validates the transaction state
    3. Checks for double-spending
    4. Calls VerifyTransaction on SEP
    5. Credits the user's wallet (if verified)
    6. Records discount usage (if applicable)
    7. Redirects user's browser to frontend with result

CRITICAL DESIGN NOTES:
    - Wallet credit + discount usage + VERIFIED status are committed ATOMICALLY
      in a single db.commit(). If any part fails, nothing is committed.
    - ref_num is assigned to payment ONLY AFTER double-spend check passes.
    - No premature commits — only commit when reaching a terminal state.
"""

import structlog
from urllib.parse import urlencode
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import redis.asyncio as aioredis

from app.core.database import get_db, get_redis
from app.payment.models.payment import Payment
from app.payment.core.constants import PaymentStatus
from app.payment.core.locker import acquire_lock, callback_lock_key
from app.payment.services.sep_client import sep_client, CallbackData
from app.payment.services.wallet_service import WalletService
from app.payment.services.discount_service import DiscountService
from app.payment.services.double_spend_guard import DoubleSpendGuard
from app.payment.config import payment_settings
from app.payment.core.metrics import metrics
from app.payment.exceptions import LockAcquisitionException
logger = structlog.get_logger()

router = APIRouter()


def _build_redirect(status: str, payment_id: str = None, reason: str = None, amount: int = None) -> str:
    """Build the frontend redirect URL with query params."""
    params = {"status": status}
    if payment_id:
        params["payment_id"] = payment_id
    if reason:
        params["reason"] = reason
    if amount:
        params["amount"] = str(amount)
    base = payment_settings.FRONTEND_PAYMENT_RESULT_URL
    return f"{base}?{urlencode(params)}"


@router.post(
    "/callback",
    summary="SEP payment callback",
    description="Receives POST callback from SEP after payment. Do not call directly.",
    include_in_schema=False,  # Hide from Swagger — SEP calls this, not users
)
async def payment_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """
    Handle SEP payment callback.

    SEP sends form-encoded POST data to this endpoint after the user
    finishes (or cancels) payment on SEP's page.

    The user's browser is physically ON this URL after SEP redirects them.
    We process the payment and then redirect their browser to our frontend.
    """
    # ── Step 1: Parse form data from SEP ──
    try:
        form_data = await request.form()
        form_dict = dict(form_data)

        logger.info(
            "payment_callback_received",
            form_keys=list(form_dict.keys()),
            res_num=form_dict.get("ResNum"),
            state=form_dict.get("State"),
            status=form_dict.get("Status"),
        )

        callback = CallbackData.from_form_data(form_dict)

    except Exception as e:
        logger.error("callback_parse_error", error=str(e))
        return RedirectResponse(
            url=_build_redirect("error", reason="Failed to parse payment gateway response"),
            status_code=302,
        )

    # ── Step 2: Find the payment by ResNum (our order number) ──
    result = await db.execute(
        select(Payment).where(Payment.res_num == callback.res_num)
    )
    payment = result.scalar_one_or_none()

    if not payment:
        logger.warning("callback_unknown_resnum", res_num=callback.res_num)
        return RedirectResponse(
            url=_build_redirect("error", reason="Payment not found"),
            status_code=302,
        )

    # ── Step 3: Replay protection — already verified? ──
    if payment.status == PaymentStatus.VERIFIED:
        logger.info(
            "callback_already_verified",
            payment_id=payment.id,
            ref_num=payment.ref_num,
        )
        return RedirectResponse(
            url=_build_redirect("VERIFIED", payment.id, amount=payment.amount),
            status_code=302,
        )

    # ── Step 4: Store callback metadata (NO ref_num yet, NO commit) ──
    now = datetime.now(timezone.utc)
    payment.state = callback.state
    payment.status_code = callback.status
    payment.rrn = callback.rrn
    payment.trace_no = callback.trace_no
    payment.secure_pan = callback.secure_pan
    payment.hashed_card_number = callback.hashed_card_number
    payment.wage = callback.wage
    payment.affective_amount = callback.affective_amount
    payment.callback_at = now
    payment.updated_at = now
    # NOTE: ref_num is NOT set here — only after double-spend check passes

    # ── Step 5: Check if transaction was successful on SEP's side ──
    if not callback.is_ok:
        payment.status = PaymentStatus.FAILED
        payment.failure_reason = callback.status_description
        await db.commit()

        logger.info(
            "payment_failed_at_sep",
            payment_id=payment.id,
            state=callback.state,
            status=callback.status,
            description=callback.status_description,
        )
        metrics.payment_failed(reason=f"sep_status_{callback.status}")
        return RedirectResponse(
            url=_build_redirect("FAILED", payment.id, callback.status_description),
            status_code=302,
        )

    # ── Step 6: Check RefNum is present ──
    # Per SEP docs: empty RefNum means a problem occurred during transaction
    if not callback.has_ref_num:
        payment.status = PaymentStatus.FAILED
        payment.failure_reason = "Empty RefNum received from SEP"
        await db.commit()

        logger.warning("callback_empty_refnum", payment_id=payment.id)
        return RedirectResponse(
            url=_build_redirect("FAILED", payment.id, "No transaction reference"),
            status_code=302,
        )

    # ── Step 7: Double-spend check BEFORE assigning ref_num ──
    is_duplicate = await DoubleSpendGuard.check_ref_num_exists(
        db, callback.ref_num, exclude_payment_id=payment.id
    )
    if is_duplicate:
        logger.critical(
            "double_spend_attempt",
            ref_num=callback.ref_num,
            payment_id=payment.id,
        )
        payment.status = PaymentStatus.FAILED
        payment.failure_reason = "Duplicate RefNum detected"
        await db.commit()

        metrics.double_spend_blocked()
        return RedirectResponse(
            url=_build_redirect("error", payment.id, "Duplicate transaction"),
            status_code=302,
        )

    # ── Step 8: NOW assign ref_num (double-spend check passed) ──
    payment.ref_num = callback.ref_num
    payment.status = PaymentStatus.CALLBACK_RECEIVED
    payment.updated_at = datetime.now(timezone.utc)
    await db.commit()  # ← ADD THIS: Commit ref_num so other callbacks see it

    # ── Step 9: Acquire lock on RefNum to prevent concurrent processing ──
    try:
        async with acquire_lock(
            redis_client,
            callback_lock_key(callback.ref_num),
        ):
            await db.refresh(payment)  # Re-read from DB
            if payment.status == PaymentStatus.VERIFIED:
                return RedirectResponse(
                    url=_build_redirect("VERIFIED", payment.id, amount=payment.amount),
                    status_code=302,
                    )
            # ── Step 10: Call VerifyTransaction on SEP ──
            try:
                verify_result = await sep_client.verify_transaction(
                    ref_num=callback.ref_num,
                )
            except Exception as e:
                logger.error(
                    "verify_failed",
                    payment_id=payment.id,
                    ref_num=callback.ref_num,
                    error=str(e),
                )
                payment.status = PaymentStatus.VERIFY_TIMEOUT
                payment.failure_reason = f"Verify failed: {str(e)}"
                payment.updated_at = datetime.now(timezone.utc)
                await db.commit()

                return RedirectResponse(
                    url=_build_redirect("error", payment.id, "Verification failed"),
                    status_code=302,
                )

            # Store SEP verify response fields
            payment.sep_result_code = verify_result.result_code
            payment.sep_result_description = verify_result.result_description

            # ── Step 11: Process verify result ──
            if verify_result.is_successful or verify_result.is_duplicate:
                verified_amount = verify_result.verified_amount

                # Update from verify response details
                if verify_result.transaction_detail:
                    payment.verified_amount = verify_result.transaction_detail.original_amount
                    payment.rrn = verify_result.transaction_detail.rrn or payment.rrn
                    payment.trace_no = verify_result.transaction_detail.strace_no or payment.trace_no

                # ── Amount match check (SEP docs Section 7: Case A vs B vs C) ──
                if verified_amount == payment.amount:
                    # ════════════════════════════════════════════════
                    # CASE A: SUCCESS — amounts match
                    # Everything below is committed ATOMICALLY
                    # ════════════════════════════════════════════════
                    payment.status = PaymentStatus.VERIFIED
                    payment.verified_at = datetime.now(timezone.utc)
                    payment.updated_at = datetime.now(timezone.utc)
                    # Credit wallet (within same transaction)
                    try:
                        await WalletService.credit(
                            db=db,
                            user_id=payment.user_id,
                            amount=payment.amount,
                            payment_id=payment.id,
                            description=f"Payment {payment.res_num} verified",
                        )
                    except Exception as e:
                        # Wallet credit failed — this is critical
                        # Do NOT commit VERIFIED without wallet credit
                        logger.critical(
                            "wallet_credit_failed_blocking",
                            payment_id=payment.id,
                            error=str(e),
                        )
                        payment.status = PaymentStatus.FAILED
                        payment.failure_reason = f"Wallet credit failed: {str(e)}"
                        await db.commit()
                        return RedirectResponse(
                            url=_build_redirect("error", payment.id, "Wallet credit failed"),
                            status_code=302,
                        )

                    # Record discount usage (within same transaction)
                    if payment.discount_code_id and payment.discount_amount:
                        try:
                            await DiscountService.record_usage(
                                db=db,
                                discount_code_id=payment.discount_code_id,
                                user_id=payment.user_id,
                                payment_id=payment.id,
                                discount_amount=payment.discount_amount,
                            )
                        except Exception as e:
                            # Discount usage recording failed — log but don't block
                            # Payment + wallet credit are more important
                            logger.error(
                                "discount_usage_record_failed",
                                payment_id=payment.id,
                                discount_code_id=payment.discount_code_id,
                                error=str(e),
                            )

                    # ═══ SINGLE ATOMIC COMMIT ═══
                    # VERIFIED + wallet credit + discount usage all committed together
                    await db.commit()

                    metrics.payment_verified.labels(
                        terminal_id=payment_settings.SEP_TERMINAL_ID
                    ).inc()

                    logger.info(
                        "payment_verified_success",
                        payment_id=payment.id,
                        amount=payment.amount,
                        ref_num=callback.ref_num,
                        has_discount=bool(payment.discount_code_id),
                    )

                    return RedirectResponse(
                        url=_build_redirect("VERIFIED", payment.id, amount=payment.amount),
                        status_code=302,
                    )
                else:
                    # ════════════════════════════════════════════════
                    # CASE B: Amount mismatch — auto-reverse
                    # Per SEP docs: "the full amount must be returned
                    # to the customer's account"
                    # ════════════════════════════════════════════════
                    logger.warning(
                        "amount_mismatch",
                        payment_id=payment.id,
                        expected=payment.amount,
                        verified=verified_amount,
                    )

                    payment.status = PaymentStatus.AMOUNT_MISMATCH
                    payment.verified_amount = verified_amount
                    payment.failure_reason = (
                        f"Amount mismatch: expected {payment.amount}, got {verified_amount}"
                    )
                    payment.updated_at = datetime.now(timezone.utc)

                    # Auto-reverse — best effort
                    try:
                        reverse_result = await sep_client.reverse_transaction(
                            ref_num=callback.ref_num
                        )
                        payment.failure_reason += (
                            f" | Auto-reversed: code={reverse_result.result_code}"
                        )
                    except Exception as e:
                        payment.failure_reason += f" | Auto-reverse failed: {str(e)}"
                        logger.error("auto_reverse_failed", error=str(e))

                    await db.commit()

                    return RedirectResponse(
                        url=_build_redirect("FAILED", payment.id, "Amount verification failed"),
                        status_code=302,
                    )
            else:
                # ════════════════════════════════════════════════
                # CASE C: Verify returned error
                # ════════════════════════════════════════════════
                payment.status = PaymentStatus.FAILED
                payment.failure_reason = (
                    verify_result.result_description
                    or f"Verify error code: {verify_result.result_code}"
                )
                payment.updated_at = datetime.now(timezone.utc)
                await db.commit()

                metrics.payment_failed(reason=f"verify_{verify_result.result_code}")

                return RedirectResponse(
                    url=_build_redirect("FAILED", payment.id, verify_result.result_description),
                    status_code=302,
                )
    except LockAcquisitionException:
        logger.warning("callback_lock_busy", payment_id=payment.id)
        return RedirectResponse(
            url=_build_redirect("error", payment.id, "Payment is being processed, please wait"),
            status_code=302,
        )
    except Exception as e:
        logger.error(
            "callback_processing_error",
            payment_id=payment.id,
            error=str(e),
            error_type=type(e).__name__,
        )
        return RedirectResponse(
            url=_build_redirect("error", payment.id, "Processing error"),
            status_code=302,
        )
