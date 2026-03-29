"""
Payment Query Router.

GET /list — List current user's payments (paginated, filtered).
GET /{payment_id} — Get details of a specific payment.

Note: /{payment_id}/reverses is handled by reverse.py router,
NOT here — to avoid duplicate route registration.
"""

import structlog
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.payment.models.reverse import Reverse
from app.payment.core.constants import PaymentStatus
from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.payment.models.payment import Payment
from app.payment.schemas.payment import (
    PaymentDetailResponse,
    PaymentListResponse,
)
from app.payment.exceptions import PaymentNotFoundException

logger = structlog.get_logger()

router = APIRouter()


# ── IMPORTANT: /list MUST come BEFORE /{payment_id} ──

@router.get(
    "/list",
    response_model=PaymentListResponse,
    summary="List payments",
    description="List current user's payments with optional filters.",
)
async def list_payments(
    status: Optional[str] = Query(None, description="Filter by payment status"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List current user's payments."""
    query = select(Payment).where(Payment.user_id == current_user.id)

    if status:
        query = query.where(Payment.status == status)

    count_query = select(func.count(Payment.id)).where(Payment.user_id == current_user.id)
    if status:
        count_query = count_query.where(Payment.status == status)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(Payment.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    payments = result.scalars().all()

    return PaymentListResponse(
        total=total,
        limit=limit,
        offset=offset,
        payments=[
            PaymentDetailResponse.model_validate(p)
            for p in payments
        ],
    )


@router.get(
    "/{payment_id}",
    response_model=PaymentDetailResponse,
    summary="Get payment details",
    description="Get details of a specific payment by ID.",
)
async def get_payment(
    payment_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific payment's details."""
    result = await db.execute(
        select(Payment).where(
            Payment.id == payment_id,
            Payment.user_id == current_user.id,
        )
    )
    payment = result.scalar_one_or_none()

    if not payment:
        raise PaymentNotFoundException(payment_id=payment_id)

    return PaymentDetailResponse.model_validate(payment)
