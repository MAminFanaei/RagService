"""
Discount Service

Manages discount codes for payment transactions.
Supports two discount types:
    - PERCENTAGE: e.g., 20% off, optionally capped at max_discount
    - FIXED: e.g., 50,000 Rials flat discount

Business Rules:
    - One discount code per transaction (no stacking)
    - Each code has max_uses (total) and per_user_limit
    - Codes have validity windows (valid_from → valid_until)
    - Minimum purchase amount can be enforced (min_purchase)
    - Discount cannot exceed the payment amount
"""

import uuid
import structlog
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func

from app.payment.models.discount import DiscountCode, DiscountUsage
from app.payment.core.constants import DiscountType
from app.payment.core.metrics import metrics
from app.payment.exceptions import (
    InvalidDiscountException,
    DiscountCodeNotFoundException,
)

logger = structlog.get_logger()


class DiscountService:
    """Discount code operations — create, validate, calculate, record usage."""

    @staticmethod
    async def create_discount_code(
        db: AsyncSession,
        code: str,
        discount_type: str,
        discount_value: int,
        max_discount: Optional[int] = None,
        min_purchase: int = 0,
        max_uses: Optional[int] = None,
        per_user_limit: int = 1,
        valid_from: Optional[datetime] = None,
        valid_until: Optional[datetime] = None,
        description: str = "",
    ) -> DiscountCode:
        # Validate
        if discount_type == DiscountType.PERCENTAGE:
            if discount_value < 1 or discount_value > 100:
                raise InvalidDiscountException(
                    code=code,
                    reason="Percentage must be between 1 and 100",
                )
        elif discount_type == DiscountType.FIXED:
            if discount_value <= 0:
                raise InvalidDiscountException(
                    code=code,
                    reason="Fixed discount must be positive",
                )
        else:
            raise InvalidDiscountException(
                code=code,
                reason=f"Invalid discount type: {discount_type}",
            )

        # Check for duplicate code — raise 409, not 400
        existing = await db.execute(
            select(DiscountCode).where(DiscountCode.code == code.upper())
        )
        if existing.scalar_one_or_none():
            from app.payment.exceptions import DuplicateDiscountCodeException
            raise DuplicateDiscountCodeException(code=code)

        # Ensure datetimes are timezone-aware
        now = datetime.now(timezone.utc)

        if valid_from and valid_from.tzinfo is None:
            valid_from = valid_from.replace(tzinfo=timezone.utc)
        if valid_until and valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)

        discount = DiscountCode(
            id=str(uuid.uuid4()),
            code=code.upper(),
            discount_type=discount_type,
            discount_value=discount_value,
            max_discount=max_discount,
            min_purchase=min_purchase,
            max_uses=max_uses,
            used_count=0,
            per_user_limit=per_user_limit,
            valid_from=valid_from or now,
            valid_until=valid_until,
            is_active=True,
            description=description,
        )
        db.add(discount)
        await db.flush()

        logger.info(
            "discount_code_created",
            code=code.upper(),
            discount_type=discount_type,
            discount_value=discount_value,
        )

        return discount

    @staticmethod
    async def validate_and_calculate(
        db: AsyncSession,
        code: str,
        user_id: str,
        amount: int,
    ) -> dict:
        result = await db.execute(
            select(DiscountCode).where(DiscountCode.code == code.upper())
        )
        discount = result.scalar_one_or_none()

        if not discount:
            raise DiscountCodeNotFoundException(code=code)

        if not discount.is_active:
            raise InvalidDiscountException(code=code, reason="Code is inactive")

        # Check validity window — ensure timezone-aware comparison
        now = datetime.now(timezone.utc)

        if discount.valid_from:
            valid_from = discount.valid_from
            if valid_from.tzinfo is None:
                valid_from = valid_from.replace(tzinfo=timezone.utc)
            if now < valid_from:
                raise InvalidDiscountException(code=code, reason="Code is not yet valid")

        if discount.valid_until:
            valid_until = discount.valid_until
            if valid_until.tzinfo is None:
                valid_until = valid_until.replace(tzinfo=timezone.utc)
            if now > valid_until:
                raise InvalidDiscountException(code=code, reason="Code has expired")

        # Check max_uses
        if discount.max_uses is not None and discount.used_count >= discount.max_uses:
            raise InvalidDiscountException(
                code=code, reason="Code has reached maximum uses",
            )

        # Check per-user usage
        user_usage_result = await db.execute(
            select(func.count(DiscountUsage.id)).where(
                DiscountUsage.discount_code_id == discount.id,
                DiscountUsage.user_id == user_id,
            )
        )
        user_usage_count = user_usage_result.scalar()

        if user_usage_count >= discount.per_user_limit:
            raise InvalidDiscountException(
                code=code,
                reason=f"You have already used this code {user_usage_count} time(s)",
            )

        # Check minimum purchase
        if amount < discount.min_purchase:
            raise InvalidDiscountException(
                code=code,
                reason=f"Minimum purchase amount is {discount.min_purchase:,} Rials",
            )

        # Calculate discount amount
        discount_amount = discount.calculate_discount(amount)
        final_amount = amount - discount_amount

        if final_amount <= 0:
            final_amount = 1
            discount_amount = amount - 1

        return {
            "discount_code_id": discount.id,
            "code": discount.code,
            "discount_type": discount.discount_type,
            "discount_value": discount.discount_value,
            "discount_amount": discount_amount,
            "original_amount": amount,
            "final_amount": final_amount,
            "max_discount": discount.max_discount,
        }

    @staticmethod
    async def record_usage(
        db: AsyncSession,
        discount_code_id: str,
        user_id: str,
        payment_id: str,
        discount_amount: int,
    ) -> DiscountUsage:
        """
        Record that a discount code was used in a payment.
        
        Called after successful payment verification.
        Also increments the used_count on the discount code.
        
        Args:
            db: Database session.
            discount_code_id: The discount code that was used.
            user_id: Who used it.
            payment_id: Which payment it was applied to.
            discount_amount: How much was discounted.
        
        Returns:
            The created DiscountUsage record.
        """
        # Create usage record
        usage = DiscountUsage(
            id=str(uuid.uuid4()),
            discount_code_id=discount_code_id,
            user_id=user_id,
            payment_id=payment_id,
            discount_amount=discount_amount,
        )
        db.add(usage)
        
        # Increment used_count atomically
        await db.execute(
            update(DiscountCode)
            .where(DiscountCode.id == discount_code_id)
            .values(used_count=DiscountCode.used_count + 1)
        )
        
        await db.flush()
        
        logger.info(
            "discount_used",
            discount_code_id=discount_code_id,
            user_id=user_id,
            payment_id=payment_id,
            discount_amount=discount_amount,
        )
        
        metrics.discount_used()
        
        return usage
