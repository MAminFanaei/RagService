"""
Reverse Model

Tracks full transaction reversals through the SEP ReverseTransaction API.

SEP Reverse Rules (from documentation):
    - Only VERIFIED payments can be reversed
    - Must be done within 50 minutes of the original transaction
    - Uses the same RefNum from the original payment
    - API endpoint: POST /verifyTxnRandomSessionkey/ipg/ReverseTransaction
    - Parameters: RefNum (string), TerminalNumber (int)
    - Response format is same as VerifyTransaction

When a reverse succeeds:
    1. Reverse record status → COMPLETED
    2. Original payment status → REVERSED
    3. User wallet debited by the payment amount
    4. Wallet transaction recorded with type=DEBIT

When a reverse fails:
    1. Reverse record status → FAILED
    2. Original payment status stays VERIFIED
    3. No wallet changes
    4. failure details stored in result_code / result_description
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, BigInteger, Integer, Text, DateTime,
    Enum, ForeignKey,
)
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.payment.core.constants import ReverseStatus


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Reverse(Base):
    __tablename__ = "reverses"

    # ── Primary Key ──────────────────────────────────────────
    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Reverse operation UUID",
    )

    # ── Payment Reference ────────────────────────────────────
    payment_id = Column(
        String(36),
        ForeignKey("payments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="FK to payments.id — which payment is being reversed",
    )

    # ── Reverse Details ──────────────────────────────────────
    ref_num = Column(
        String(50),
        nullable=False,
        comment="RefNum of the original payment (sent to SEP Reverse API)",
    )
    amount = Column(
        BigInteger,
        nullable=False,
        comment="Amount being reversed (full amount of original payment, in Rials)",
    )
    reason = Column(
        Text,
        nullable=True,
        comment="Reason for reversal (for internal records)",
    )

    # ── Status ───────────────────────────────────────────────
    status = Column(
        Enum(ReverseStatus, native_enum=False, length=20),
        nullable=False,
        default=ReverseStatus.PENDING,
        index=True,
        comment="Reverse lifecycle status",
    )

    # ── SEP Response Fields ──────────────────────────────────
    result_code = Column(
        Integer,
        nullable=True,
        comment="SEP ResultCode from ReverseTransaction API (0=success)",
    )
    result_description = Column(
        Text,
        nullable=True,
        comment="SEP ResultDescription from ReverseTransaction API",
    )

    # ── Timestamps ───────────────────────────────────────────
    created_at = Column(
        DateTime(timezone=True),
        default=_utc_now,
        nullable=False,
        comment="When reverse was requested",
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        nullable=False,
        comment="Last update to this reverse record",
    )

    # ── Relationships ────────────────────────────────────────
    payment = relationship(
        "Payment",
        back_populates="reverses",
    )

    def __repr__(self) -> str:
        return (
            f"<Reverse(id={self.id}, payment_id={self.payment_id}, "
            f"amount={self.amount}, status={self.status})>"
        )
