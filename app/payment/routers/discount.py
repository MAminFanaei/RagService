"""
Discount Code Router

POST /discount/create — Create a new discount code (admin only).
POST /discount/validate — Validate a discount code and preview discount (any user).

Authentication:
    - /create: Requires admin JWT
    - /validate: Requires any authenticated user JWT
"""

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import get_current_user, get_current_admin_user
from app.models.user import User
from app.payment.schemas.discount import (
    DiscountCreateRequest,
    DiscountValidateRequest,
    DiscountValidateResponse,
    DiscountDetailResponse,
)
from app.payment.services.discount_service import DiscountService
from app.payment.exceptions import (
    InvalidDiscountException,
    DiscountCodeNotFoundException,
)

logger = structlog.get_logger()

router = APIRouter()


@router.post(
    "/create",
    response_model=DiscountDetailResponse,
    summary="Create a discount code (admin)",
    description=(
        "Create a new discount code. Admin access required. "
        "Supports PERCENTAGE (with optional cap) and FIXED discount types."
    ),
    responses={
        200: {
            "description": "Discount code created",
            "content": {
                "application/json": {
                    "example": {
                        "id": "uuid-xxx",
                        "code": "WELCOME20",
                        "discount_type": "PERCENTAGE",
                        "discount_value": 20,
                        "max_discount": 200000,
                        "min_purchase": 100000,
                        "max_uses": 1000,
                        "used_count": 0,
                        "per_user_limit": 1,
                        "valid_from": "2024-01-01T00:00:00Z",
                        "valid_until": "2024-12-31T23:59:59Z",
                        "is_active": True,
                        "created_at": "2024-01-01T00:00:00Z",
                    }
                }
            },
        },
        400: {"description": "Invalid discount parameters"},
        401: {"description": "Authentication required"},
        403: {"description": "Admin access required"},
        409: {"description": "Discount code already exists"},
    },
)
async def create_discount_code(
    request: DiscountCreateRequest,
    admin_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new discount code (admin only)."""
    logger.info(
        "discount_create_request",
        admin_id=admin_user.id,
        code=request.code,
        discount_type=request.discount_type,
        discount_value=request.discount_value,
    )

    discount = await DiscountService.create_discount_code(
        db=db,
        code=request.code,
        discount_type=request.discount_type,
        discount_value=request.discount_value,
        max_discount=request.max_discount,
        min_purchase=request.min_purchase,
        max_uses=request.max_uses,
        per_user_limit=request.per_user_limit,
        valid_from=request.valid_from,
        valid_until=request.valid_until,
        description=request.description,
    )
    await db.commit()

    return DiscountDetailResponse(
        id=discount.id,
        code=discount.code,
        discount_type=discount.discount_type,
        discount_value=discount.discount_value,
        max_discount=discount.max_discount,
        min_purchase=discount.min_purchase,
        max_uses=discount.max_uses,
        used_count=discount.used_count,
        per_user_limit=discount.per_user_limit,
        valid_from=discount.valid_from,
        valid_until=discount.valid_until,
        is_active=discount.is_active,
        created_at=discount.created_at,
    )


@router.post(
    "/validate",
    response_model=DiscountValidateResponse,
    summary="Validate a discount code",
    description=(
        "Check if a discount code is valid for the given amount and preview "
        "the discount. Does NOT consume the code — it's just a preview. "
        "The code is consumed when you call /initiate with the discount_code."
    ),
    responses={
        200: {
            "description": "Validation result",
            "content": {
                "application/json": {
                    "examples": {
                        "valid": {
                            "summary": "Valid code",
                            "value": {
                                "valid": True,
                                "code": "WELCOME20",
                                "discount_type": "PERCENTAGE",
                                "discount_value": 20,
                                "discount_amount": 100000,
                                "final_amount": 400000,
                                "original_amount": 500000,
                                "message": "20% discount applied",
                            },
                        },
                        "invalid": {
                            "summary": "Invalid code",
                            "value": {
                                "valid": False,
                                "code": "EXPIRED50",
                                "message": "This discount code has expired",
                            },
                        },
                    }
                }
            },
        },
        401: {"description": "Authentication required"},
    },
)
async def validate_discount_code(
    request: DiscountValidateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Validate a discount code and preview the discount.

    This is a preview — the code is NOT consumed.
    Use /initiate with discount_code to actually apply it.
    """
    try:
        result = await DiscountService.validate_and_calculate(
            db=db,
            code=request.code,
            user_id=current_user.id,
            amount=request.amount,
        )

        return DiscountValidateResponse(
            valid=True,
            code=result["code"],
            discount_type=result["discount_type"],
            discount_value=result["discount_value"],
            discount_amount=result["discount_amount"],
            final_amount=result["final_amount"],
            original_amount=result["original_amount"],
            message=_format_discount_message(result),
        )

    except (InvalidDiscountException, DiscountCodeNotFoundException) as e:
        return DiscountValidateResponse(
            valid=False,
            code=request.code.upper(),
            message=e.message,
        )


def _format_discount_message(result: dict) -> str:
    """Format a human-readable discount message."""
    if result["discount_type"] == "PERCENTAGE":
        msg = f"{result['discount_value']}% discount"
        if result.get("max_discount"):
            msg += f" (max {result['max_discount']:,} Rials)"
        msg += f" = {result['discount_amount']:,} Rials off"
    else:
        msg = f"{result['discount_amount']:,} Rials off"

    return msg
