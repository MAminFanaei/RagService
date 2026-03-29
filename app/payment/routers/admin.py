"""
Admin Payment Router

Admin-only endpoints for monitoring and managing all users' payments.

All endpoints require admin JWT authentication via get_current_admin_user.

Endpoints:
    GET  /admin/overview                    — Dashboard stats
    GET  /admin/payments                    — List all payments (filterable)
    GET  /admin/payments/{payment_id}       — Full detail of any payment
    GET  /admin/payments/{payment_id}/reverses — Reverses for any payment
"""

import structlog
from typing import Optional
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.api.deps import get_current_admin_user
from app.models.user import User
from app.payment.models.payment import Payment
from app.payment.models.reverse import Reverse
from app.payment.core.constants import PaymentStatus
from app.payment.exceptions import PaymentNotFoundException
from app.payment.schemas.admin import (
    AdminPaymentDetailResponse,
    AdminPaymentListResponse,
    AdminReverseDetailResponse,
    AdminReverseListResponse,
    AdminOverviewResponse,
    StatusCount,
)

logger = structlog.get_logger()

router = APIRouter()


@router.get(
    "/overview",
    response_model=AdminOverviewResponse,
    summary="Payment dashboard overview",
    description="Get payment stats, revenue totals, and alerts for stuck/failed payments.",
)
async def admin_overview(
    admin_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin dashboard with payment statistics and alerts."""
    logger.info("admin_overview_request", admin_id=admin_user.id)

    # Total payments
    total_result = await db.execute(select(func.count(Payment.id)))
    total_payments = total_result.scalar() or 0

    # Total verified revenue
    revenue_result = await db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == PaymentStatus.VERIFIED
        )
    )
    total_verified_amount = revenue_result.scalar() or 0

    # Counts by status
    status_counts = []
    for status in PaymentStatus:
        count_result = await db.execute(
            select(func.count(Payment.id)).where(Payment.status == status)
        )
        count = count_result.scalar() or 0

        amount_result = await db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == status
            )
        )
        amount = amount_result.scalar() or 0

        if count > 0:
            status_counts.append(StatusCount(
                status=status.value,
                count=count,
                total_amount=amount,
            ))

    # Alerts: stuck in CALLBACK_RECEIVED
    stuck_cb_result = await db.execute(
        select(func.count(Payment.id)).where(
            Payment.status == PaymentStatus.CALLBACK_RECEIVED
        )
    )
    stuck_callback_received = stuck_cb_result.scalar() or 0

    # Alerts: VERIFY_TIMEOUT
    stuck_vt_result = await db.execute(
        select(func.count(Payment.id)).where(
            Payment.status == PaymentStatus.VERIFY_TIMEOUT
        )
    )
    stuck_verify_timeout = stuck_vt_result.scalar() or 0

    # Alerts: AMOUNT_MISMATCH
    mismatch_result = await db.execute(
        select(func.count(Payment.id)).where(
            Payment.status == PaymentStatus.AMOUNT_MISMATCH
        )
    )
    amount_mismatches = mismatch_result.scalar() or 0

    # Alerts: failures in last 24h
    yesterday = datetime.now(timezone.utc) - timedelta(hours=24)
    failures_result = await db.execute(
        select(func.count(Payment.id)).where(
            Payment.status == PaymentStatus.FAILED,
            Payment.updated_at >= yesterday,
        )
    )
    recent_failures_24h = failures_result.scalar() or 0

    return AdminOverviewResponse(
        total_payments=total_payments,
        total_verified_amount=total_verified_amount,
        by_status=status_counts,
        stuck_callback_received=stuck_callback_received,
        stuck_verify_timeout=stuck_verify_timeout,
        amount_mismatches=amount_mismatches,
        recent_failures_24h=recent_failures_24h,
    )


@router.get(
    "/payments",
    response_model=AdminPaymentListResponse,
    summary="List all payments (admin)",
    description="List payments across all users with optional filters.",
)
async def admin_list_payments(
    status: Optional[str] = Query(None, description="Filter by payment status"),
    user_id: Optional[str] = Query(None, description="Filter by user ID"),
    created_after: Optional[datetime] = Query(None, description="Filter by creation date"),
    created_before: Optional[datetime] = Query(None, description="Filter by creation date"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    admin_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List all payments with filters — admin only."""
    logger.info(
        "admin_list_payments",
        admin_id=admin_user.id,
        filters={"status": status, "user_id": user_id},
    )

    query = select(Payment)
    count_query = select(func.count(Payment.id))

    # Apply filters
    if status:
        query = query.where(Payment.status == status)
        count_query = count_query.where(Payment.status == status)
    if user_id:
        query = query.where(Payment.user_id == user_id)
        count_query = count_query.where(Payment.user_id == user_id)
    if created_after:
        query = query.where(Payment.created_at >= created_after)
        count_query = count_query.where(Payment.created_at >= created_after)
    if created_before:
        query = query.where(Payment.created_at <= created_before)
        count_query = count_query.where(Payment.created_at <= created_before)

    # Count
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Fetch paginated
    query = query.order_by(Payment.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    payments = result.scalars().all()

    return AdminPaymentListResponse(
        total=total,
        limit=limit,
        offset=offset,
        payments=[
            AdminPaymentDetailResponse.model_validate(p)
            for p in payments
        ],
    )


@router.get(
    "/payments/{payment_id}",
    response_model=AdminPaymentDetailResponse,
    summary="Get payment details (admin)",
    description="Get full details of any payment including all SEP fields.",
)
async def admin_get_payment(
    payment_id: str,
    admin_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Get full payment details — admin only, no user_id restriction."""
    logger.info(
        "admin_get_payment",
        admin_id=admin_user.id,
        payment_id=payment_id,
    )

    result = await db.execute(
        select(Payment).where(Payment.id == payment_id)
    )
    payment = result.scalar_one_or_none()

    if not payment:
        raise PaymentNotFoundException(payment_id=payment_id)

    return AdminPaymentDetailResponse.model_validate(payment)


@router.get(
    "/payments/{payment_id}/reverses",
    response_model=AdminReverseListResponse,
    summary="List reverses for any payment (admin)",
    description="Get all reverse attempts for any payment.",
)
async def admin_list_reverses(
    payment_id: str,
    admin_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List all reverses for any payment — admin only."""
    logger.info(
        "admin_list_reverses",
        admin_id=admin_user.id,
        payment_id=payment_id,
    )

    # Verify payment exists (no user_id filter — admin sees all)
    pay_result = await db.execute(
        select(Payment).where(Payment.id == payment_id)
    )
    payment = pay_result.scalar_one_or_none()

    if not payment:
        raise PaymentNotFoundException(payment_id=payment_id)

    # Get reverses
    result = await db.execute(
        select(Reverse)
        .where(Reverse.payment_id == payment_id)
        .order_by(Reverse.created_at.desc())
    )
    reverses = result.scalars().all()

    return AdminReverseListResponse(
        payment_id=payment_id,
        payment_status=payment.status.value if hasattr(payment.status, 'value') else str(payment.status),
        total=len(reverses),
        reverses=[
            AdminReverseDetailResponse.model_validate(r)
            for r in reverses
        ],
    )
