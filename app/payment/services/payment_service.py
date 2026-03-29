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
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.payment.config import payment_settings
from app.payment.core.constants import (
    PaymentStatus,
    RES_NUM_PREFIX,
)

from app.payment.core.metrics import metrics
from app.payment.models.payment import Payment
from app.payment.services.sep_client import sep_client
from app.payment.services.discount_service import DiscountService
from app.payment.exceptions import (
    InvalidAmountException,
    PaymentNotFoundException,
    SEPTokenException,
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
