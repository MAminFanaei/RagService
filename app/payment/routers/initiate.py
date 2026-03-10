"""
Payment Initiation Router

POST /initiate — Start a new payment flow.

Flow:
    1. Frontend sends: { amount, description?, discount_code?, cell_number? }
    2. We validate, apply discount, generate ResNum
    3. Call SEP Token API to get a token
    4. Return { payment_id, token, redirect_url, amounts }
    5. Frontend redirects user's browser to redirect_url

Authentication: Required (JWT Bearer token)
"""

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.payment.schemas.payment import (
    PaymentInitiateRequest,
    PaymentInitiateResponse,
)
from app.payment.services.payment_service import PaymentService
from app.payment.core.metrics import metrics

logger = structlog.get_logger()

router = APIRouter()


@router.post(
    "/initiate",
    response_model=PaymentInitiateResponse,
    summary="Initiate a new payment",
    description=(
        "Start a payment flow. Returns a token and redirect URL. "
        "Frontend must redirect the user's browser to the redirect_url "
        "for them to enter card details on SEP's secure page."
    ),
    responses={
        200: {
            "description": "Payment initiated successfully",
            "content": {
                "application/json": {
                    "example": {
                        "payment_id": "550e8400-e29b-41d4-a716-446655440000",
                        "res_num": "PAY-1699123456789-a1b2c3d4",
                        "token": "2c3c1fefac5a48geb9f9be7e445dd9b2",
                        "redirect_url": "https://sep.shaparak.ir/OnlinePG/SendToken?token=2c3c1fefac5a48geb9f9be7e445dd9b2",
                        "amount": 400000,
                        "original_amount": 500000,
                        "discount_applied": 100000,
                        "discount_code": "WELCOME20",
                    }
                }
            },
        },
        400: {"description": "Invalid amount or discount code"},
        401: {"description": "Authentication required"},
        502: {"description": "SEP gateway error"},
    },
)
async def initiate_payment(
    request: PaymentInitiateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Start a new payment.

    The response contains a `redirect_url` — redirect the user's browser
    to this URL. They will enter card details on SEP's secure payment page.
    After payment, SEP redirects them back to our callback endpoint.
    """
    logger.info(
        "payment_initiate_request",
        user_id=current_user.id,
        amount=request.amount,
        has_discount=bool(request.discount_code),
    )

    result = await PaymentService.initiate_payment(
        db=db,
        user_id=current_user.id,
        amount=request.amount,
        description=request.description or "",
        discount_code=request.discount_code,
        cell_number=request.cell_number,
    )

    return PaymentInitiateResponse(
        payment_id=result["payment_id"],
        res_num=result["res_num"],
        token=result["token"],
        redirect_url=result["redirect_url"],
        amount=result["amount"],
        original_amount=result["original_amount"],
        discount_amount=result["discount_amount"],
        discount_code=request.discount_code,
    )
