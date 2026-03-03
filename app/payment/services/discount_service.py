"""
Discount Code Service.

Handles discount code validation and application.
One discount code per transaction (no stacking).
"""

import structlog
from datetime import datetime, timezone
from typing import Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.payment.models.discount import DiscountCode, DiscountUsage
from app.payment.core.constants import DiscountType

logger = structlog.get_logger()


class DiscountService:
    """Service for discount code operations."""

    @staticmethod
    async def validate_code(
        db: AsyncSession,
        code: str,
        amount: int,
        user_id: str,
    ) -> Tuple[bool, int, Optional[DiscountCode]]:
        """
        Validate a discount code and calculate the discount amount.

        Returns:
            Tuple of (is_valid, discount_amount, discount_code_object)
        """
        # Find the code
        result = await db.execute(
            select(DiscountCode).where(DiscountCode.code == code)
        )
        dc = result.scalar_one_or_none()

        if not dc:
            return False, 0, None

        # Check active
        if not dc.is_active:
            return False, 0, None

        # Check validity dates — FIXED: use timezone-aware datetime
        now = datetime.now(timezone.utc)
        if dc.valid_from and now < dc.valid_from:
            return False, 0, None
        if dc.valid_until and now > dc.valid_until:
            return False, 0, None

        # Check max uses
        if dc.max_uses is not None and dc.used_count >= dc.max_uses:
            return False, 0, None

        # Check per-user limit
        user_usage_result = await db.execute(
            select(func.count(DiscountUsage.id)).where(
                DiscountUsage.discount_code_id == dc.id,
                DiscountUsage.user_id == user_id,
            )
        )
        user_usage_count = user_usage_result.scalar() or 0
        if user_usage_count >= dc.per_user_limit:
            return False, 0, None

        # Check minimum purchase
        if amount < dc.min_purchase:
            return False, 0, None

        # Calculate discount
        discount_amount = dc.calculate_discount(amount)

        return True, discount_amount, dc

    @staticmethod
    async def record_usage(
        db: AsyncSession,
        discount_code: DiscountCode,
        user_id: str,
        payment_id: str,
        discount_amount: int,
    ) -> DiscountUsage:
        """Record that a user used a discount code."""
        usage = DiscountUsage(
            discount_code_id=discount_code.id,
            user_id=user_id,
            payment_id=payment_id,
            discount_amount=discount_amount,
        )
        db.add(usage)

        # Increment used_count
        discount_code.used_count = (discount_code.used_count or 0) + 1
        db.add(discount_code)

        await db.commit()
        await db.refresh(usage)

        logger.info(
            "discount_used",
            code=discount_code.code,
            user_id=user_id,
            payment_id=payment_id,
            discount_amount=discount_amount,
        )

        return usage

    @staticmethod
    async def create_code(
        db: AsyncSession,
        code: str,
        discount_type: str,
        discount_value: int,
        description: str = None,
        max_discount: int = None,
        min_purchase: int = 0,
        max_uses: int = None,
        per_user_limit: int = 1,
        valid_from: datetime = None,
        valid_until: datetime = None,
    ) -> DiscountCode:
        """Create a new discount code (admin only)."""
        now = datetime.now(timezone.utc)

        dc = DiscountCode(
            code=code.upper(),
            description=description,
            discount_type=discount_type,
            discount_value=discount_value,
            max_discount=max_discount,
            min_purchase=min_purchase,
            max_uses=max_uses,
            per_user_limit=per_user_limit,
            valid_from=valid_from or now,
            valid_until=valid_until or now,
            is_active=True,
        )
        db.add(dc)
        await db.commit()
        await db.refresh(dc)

        logger.info("discount_code_created", code=dc.code, type=discount_type)
        return dc
