"""
Reverse Transaction Router

POST /{payment_id}/reverse — Reverse a verified payment (full amount).
GET /{payment_id}/reverses — List all reverse attempts for a payment.

Authentication: Required (JWT Bearer token)
Users can only reverse their own verified payments.

SEP Constraints:
    - Only VERIFIED payments can be reversed
    - Must be within 50 minutes of original transaction
    - Full amount reversal only (no partial)
"""

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from app.core.database import get_db, get_redis
from app.api.deps import get_current_user
from app.models.user import User
from app.payment.schemas.reverse import (
    ReverseRequest,
    ReverseResponse,
    ReverseDetailResponse,
    ReverseListResponse,
)
from app.payment.services.reverse_service import ReverseService

logger = structlog.get_logger()

router = APIRouter()


@router.post(
    "/{payment_id}/reverse",
    response_model=ReverseResponse,
    summary="Reverse a payment",
    description=(
        "Reverse a verified payment. Full amount will be returned to the "
        "cardholder. Must be done within 50 minutes of the original transaction. "
        "Your wallet balance will be debited by the reversed amount."
    ),
    responses={
        200: {
            "description": "Reverse result",
            "content": {
                "application/json": {
                    "examples": {
                        "success": {
                            "summary": "Successful reverse",
                            "value": {
                                "reverse_id": "uuid-xxx",
                                "payment_id": "uuid-yyy",
                                "status": "COMPLETED",
                                "amount": 500000,
                                "message": "Transaction reversed successfully",
                            },
                        },
                        "failed": {
                            "summary": "Failed reverse",
                            "value": {
                                "reverse_id": "uuid-xxx",
                                "payment_id": "uuid-yyy",
                                "status": "FAILED",
                                "amount": 500000,
                                "message": "Reversal window expired",
                            },
                        },
                    }
                }
            },
        },
        400: {"description": "Payment not in reversible state"},
        401: {"description": "Authentication required"},
        404: {"description": "Payment not found"},
        409: {"description": "Reverse already in progress"},
    },
)
async def reverse_payment(
    payment_id: str,
    request: ReverseRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """
    Reverse a verified payment.

    The full payment amount will be returned to the cardholder's account.
    Your wallet will be debited by the same amount.
    """
    logger.info(
        "reverse_request",
        payment_id=payment_id,
        user_id=current_user.id,
        reason=request.reason,
    )

    result = await ReverseService.reverse_payment(
        db=db,
        redis_client=redis_client,
        payment_id=payment_id,
        user_id=current_user.id,
        reason=request.reason,
    )

    return ReverseResponse(
        reverse_id=result["reverse_id"],
        payment_id=result["payment_id"],
        status=result["status"],
        amount=result.get("amount", 0),
        result_code=result.get("result_code"),
        result_description=result.get("result_description"),
        message=result.get("reason", "")
        if result["status"] == "FAILED"
        else "Transaction reversed successfully",
    )


@router.get(
    "/{payment_id}/reverses",
    response_model=ReverseListResponse,
    summary="List reverse attempts",
    description="Get all reverse attempts for a specific payment.",
    responses={
        200: {"description": "List of reverse attempts"},
        401: {"description": "Authentication required"},
        404: {"description": "Payment not found"},
    },
)
async def list_reverses(
    payment_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all reverse attempts for a payment (must be yours)."""
    result = await ReverseService.list_reverses(
        db=db,
        payment_id=payment_id,
        user_id=current_user.id,
    )

    reverse_responses = []
    for rev in result["reverses"]:
        reverse_responses.append(
            ReverseDetailResponse(
                id=rev.id,
                payment_id=rev.payment_id,
                ref_num=rev.ref_num,
                reason=rev.reason,
                status=rev.status,
                result_code=rev.result_code,
                result_description=rev.result_description,
                created_at=rev.created_at,
                updated_at=rev.updated_at,
            )
        )

    return ReverseListResponse(
        payment_id=result["payment_id"],
        total=result["total"],
        reverses=reverse_responses,
    )
