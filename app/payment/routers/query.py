"""
Payment Query Router.
"""

import structlog
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.payment.models.payment import Payment
from app.payment.models.reverse import Reverse
from app.payment.core.constants import PaymentStatus
from app.payment.exceptions import PaymentNotFoundException

logger = structlog.get_logger()

router = APIRouter()


# ── IMPORTANT: /list MUST come BEFORE /{payment_id} ──

@router.get(
    "/list",
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

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "payments": [
            {
                "id": p.id,
                "res_num": p.res_num,
                "ref_num": p.ref_num,
                "amount": p.amount,
                "original_amount": p.original_amount,
                "discount_amount": p.discount_amount,
                "status": p.status,
                "state": p.state,
                "description": p.description,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in payments
        ],
    }


@router.get(
    "/{payment_id}",
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
        raise PaymentNotFoundException(f"Payment {payment_id} not found")

    return {
        "id": payment.id,
        "res_num": payment.res_num,
        "ref_num": payment.ref_num,
        "amount": payment.amount,
        "original_amount": payment.original_amount,
        "discount_amount": payment.discount_amount,
        "status": payment.status,
        "state": payment.state,
        "rrn": payment.rrn,
        "trace_no": payment.trace_no,
        "secure_pan": payment.secure_pan,
        "failure_reason": payment.failure_reason,
        "description": payment.description,
        "created_at": payment.created_at.isoformat() if payment.created_at else None,
        "verified_at": payment.verified_at.isoformat() if payment.verified_at else None,
        "updated_at": payment.updated_at.isoformat() if payment.updated_at else None,
    }


@router.get(
    "/{payment_id}/reverses",
    summary="List reverses for a payment",
    description="Get all reverse attempts for a specific payment.",
)
async def list_reverses(
    payment_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all reverses for a payment."""
    payment_result = await db.execute(
        select(Payment).where(
            Payment.id == payment_id,
            Payment.user_id == current_user.id,
        )
    )
    payment = payment_result.scalar_one_or_none()

    if not payment:
        raise PaymentNotFoundException(f"Payment {payment_id} not found")

    result = await db.execute(
        select(Reverse)
        .where(Reverse.payment_id == payment_id)
        .order_by(Reverse.created_at.desc())
    )
    reverses = result.scalars().all()

    return {
        "payment_id": payment_id,
        "reverses": [
            {
                "id": r.id,
                "payment_id": r.payment_id,
                "ref_num": r.ref_num,
                "reason": r.reason,
                "status": r.status,
                "result_code": r.result_code,
                "result_description": r.result_description,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in reverses
        ],
    }