"""
Discount Code Models.

Two tables:
    - discount_codes: Stores discount code definitions
    - discount_usages: Tracks who used what code on which payment
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, BigInteger, Integer, Boolean, DateTime,
    Enum, Text, ForeignKey, Index
)
from sqlalchemy.orm import relationship

from app.core.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class DiscountCode(Base):
    """Discount code definition."""
    __tablename__ = "discount_codes"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code = Column(String(50), unique=True, nullable=False, index=True)
    description = Column(String(255), nullable=True)  # ADDED — was missing

    # Discount configuration
    discount_type = Column(
        Enum("PERCENTAGE", "FIXED", name="discount_type_enum", native_enum=False),
        nullable=False,
    )
    discount_value = Column(BigInteger, nullable=False)
    max_discount = Column(BigInteger, nullable=True)
    min_purchase = Column(BigInteger, default=0, nullable=False)

    # Usage limits
    max_uses = Column(Integer, nullable=True)
    used_count = Column(Integer, default=0, nullable=False)
    per_user_limit = Column(Integer, default=1, nullable=False)

    # Validity
    valid_from = Column(DateTime(timezone=True), nullable=False)
    valid_until = Column(DateTime(timezone=True), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    # Relationships
    usages = relationship("DiscountUsage", back_populates="discount_code", lazy="selectin")

    __table_args__ = (
        Index("ix_discount_codes_active", "is_active", "valid_until"),
        Index("ix_discount_codes_code_active", "code", "is_active"),
    )

    def calculate_discount(self, amount: int) -> int:
        """Calculate the discount amount for a given purchase amount."""
        if self.discount_type == "PERCENTAGE":
            discount = int(amount * self.discount_value / 100)
            if self.max_discount is not None:
                discount = min(discount, self.max_discount)
            return discount
        elif self.discount_type == "FIXED":
            return min(self.discount_value, amount)
        return 0


class DiscountUsage(Base):
    """Tracks each use of a discount code."""
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

    __table_args__ = (
        Index("ix_discount_usages_user", "user_id", "discount_code_id"),
        Index("ix_discount_usages_payment", "payment_id"),
        Index("ix_discount_usages_code", "discount_code_id"),
    )
