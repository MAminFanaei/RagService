"""
Discount Code and Usage Models.

Two tables:
    - discount_codes: stores discount code definitions
    - discount_usages: tracks who used which code on which payment
"""

import uuid
from sqlalchemy import (
    Column, String, Integer, BigInteger, Boolean,
    DateTime, Enum, ForeignKey, Index,
)
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class DiscountCode(Base):
    """
    Discount code definition.

    Supports:
        - PERCENTAGE: e.g., 20% off, optionally capped at max_discount
        - FIXED: e.g., 50,000 Rials flat discount
    """
    __tablename__ = "discount_codes"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code = Column(String(50), unique=True, nullable=False, index=True)
    description = Column(String(255), nullable=True)

    # Discount configuration
    discount_type = Column(
        Enum("PERCENTAGE", "FIXED", name="discount_type_enum", native_enum=False),
        nullable=False,
    )
    discount_value = Column(BigInteger, nullable=False)  # percentage (1-100) or fixed Rials
    max_discount = Column(BigInteger, nullable=True)  # cap for percentage discounts
    min_purchase = Column(BigInteger, default=0, nullable=False)  # minimum purchase amount

    # Usage limits
    max_uses = Column(Integer, nullable=True)  # NULL = unlimited
    used_count = Column(Integer, default=0, nullable=False)
    per_user_limit = Column(Integer, default=1, nullable=False)

    # Validity window — timezone-aware
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_until = Column(DateTime(timezone=True), nullable=True)

    # Status
    is_active = Column(Boolean, default=True, nullable=False)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    # Relationships
    usages = relationship("DiscountUsage", back_populates="discount_code", lazy="selectin")
    payments = relationship("Payment", back_populates="discount_code", lazy="selectin")
    __table_args__ = (
        Index("ix_discount_codes_active", "is_active", "valid_until"),
        Index("ix_discount_codes_code_active", "code", "is_active"),
    )
    def calculate_discount(self, amount: int) -> int:
        """
        Calculate discount amount for a given purchase amount.

        Args:
            amount: Original purchase amount in Rials.

        Returns:
            Discount amount in Rials.
        """
        if self.discount_type == "PERCENTAGE":
            discount = int(amount * self.discount_value / 100)
            if self.max_discount is not None:
                discount = min(discount, self.max_discount)
            return min(discount, amount)
        elif self.discount_type == "FIXED":
            return min(self.discount_value, amount)
        return 0

    def __repr__(self):
        return f"<DiscountCode(code={self.code}, type={self.discount_type}, value={self.discount_value})>"


class DiscountUsage(Base):
    """
    Tracks usage of discount codes.

    Records which user used which code on which payment,
    enabling per-user usage limits and audit trail.
    """
    __tablename__ = "discount_usages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    discount_code_id = Column(
        String(36),
        ForeignKey("discount_codes.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    payment_id = Column(
        String(36),
        ForeignKey("payments.id", ondelete="SET NULL"),
        nullable=True,
    )
    discount_amount = Column(BigInteger, nullable=False)
    used_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    # Relationships
    discount_code = relationship("DiscountCode", back_populates="usages")
    user = relationship("User", lazy="selectin")
    payment = relationship("Payment", back_populates = "discount_usage",lazy="selectin")
    # Indexes
    __table_args__ = (
        Index("ix_discount_usages_code_user", "discount_code_id", "user_id"),
        Index("ix_discount_usages_user", "user_id"),
        Index("ix_discount_usages_payment", "payment_id"),
    )

    def __repr__(self):
        return f"<DiscountUsage(code={self.discount_code_id}, user={self.user_id}, amount={self.discount_amount})>"
