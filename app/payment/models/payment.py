"""
Payment Model

Core table for tracking payment transactions through the SEP gateway.

Lifecycle (status field):
    PENDING            → Payment initiated, no token yet
    TOKEN_OBTAINED     → Token received from SEP, user redirected to gateway
    CALLBACK_RECEIVED  → SEP callback received (user returned from gateway)
    VERIFIED           → VerifyTransaction succeeded, wallet credited
    FAILED             → Payment failed at any stage
    REVERSED           → Successfully reversed after verification

SEP callback parameters stored:
    state, status_code, rrn, ref_num, trace_no, secure_pan,
    hashed_card_number, wage, affective_amount

SEP verify response stored:
    verified_amount, sep_result_code, sep_result_description

Double-spending prevention:
    ref_num has a UNIQUE constraint — the first layer of 3-layer protection.
    See app.payment.services.double_spend_guard for layers 2 & 3.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, BigInteger, Integer, Text, DateTime,
    Enum, Index, ForeignKey,
)
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.payment.core.constants import PaymentStatus


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Payment(Base):
    __tablename__ = "payments"

    # ── Primary Key ──────────────────────────────────────────
    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Internal payment UUID",
    )

    # ── User Reference ───────────────────────────────────────
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign Key to users.id — who initiated this payment",
    )

    # ── SEP Transaction Identifiers ──────────────────────────
    #
    # res_num: Our order number sent TO SEP (we generate this)
    #   - UNIQUE to prevent duplicate payment initiation
    #   - Format: PAY-{uuid4} (see constants.RES_NUM_PREFIX)
    #
    # ref_num: SEP's digital receipt sent BACK to us
    #   - UNIQUE to prevent double-spending (Layer 1)
    #   - NULL until callback received
    #   - Max 50 chars per SEP docs
    #
    res_num = Column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
        comment="Reservation number — our order ID sent to SEP",
    )
    ref_num = Column(
        String(50),
        unique=True,
        nullable=True,
        index=True,
        comment="Reference number — SEP digital receipt (NULL until callback)",
    )

    # ── Amount Fields ────────────────────────────────────────
    #
    # original_amount: What the user originally wanted to pay
    # discount_amount: How much discount was applied
    # amount: What was actually sent to SEP (original - discount)
    #   → This is what SEP charges from the card
    #   → VerifyTransaction must return this exact amount
    #
    original_amount = Column(
        BigInteger,
        nullable=False,
        comment="Original amount before discount (Rials)",
    )
    discount_amount = Column(
        BigInteger,
        nullable=False,
        default=0,
        comment="Discount applied (Rials). 0 if no discount.",
    )
    amount = Column(
        BigInteger,
        nullable=False,
        comment="Final amount sent to SEP = original - discount (Rials)",
    )

    # ── Discount Reference ───────────────────────────────────
    discount_code_id = Column(
        String(36),
        ForeignKey("discount_codes.id", ondelete="SET NULL"),
        nullable=True,
        comment="FK to discount_codes.id if a discount was applied",
    )

    description = Column(String(255), nullable=True)
    
    # ── SEP Configuration ────────────────────────────────────
    terminal_id = Column(
        String(20),
        nullable=False,
        comment="SEP TerminalId used for this transaction",
    )

    # ── SEP Token ────────────────────────────────────────────
    token = Column(
        String(100),
        nullable=True,
        comment="SEP token received from Token API (NULL before token request)",
    )

    # ── Internal Status (Our State Machine) ──────────────────
    status = Column(
        Enum(PaymentStatus, native_enum=False, length=30),
        nullable=False,
        default=PaymentStatus.PENDING,
        index=True,
        comment="Internal payment status (our state machine)",
    )

    # ── SEP Callback Fields ──────────────────────────────────
    #
    # These are populated when SEP POSTs back to our callback URL.
    # All are NULL until the callback is received.
    #
    # state: SEP's English state string (e.g., "OK", "Failed", "CanceledByUser")
    # status_code: SEP's numeric status (e.g., 2=OK, 1=CanceledByUser, 3=Failed)
    # rrn: Bank reference number (شماره مرجع)
    # trace_no: SEP trace/tracking number (شماره رهگیری)
    # secure_pan: Masked card number (e.g., "621986****8080")
    # hashed_card_number: SHA-256 hash of card number (from SEP callback)
    # wage: Transaction fee (for merchants using multi-settlement)
    # affective_amount: Amount actually deducted (for merchants using discount system)
    #
    state = Column(
        String(30),
        nullable=True,
        comment="SEP State: OK, Failed, CanceledByUser, etc.",
    )
    status_code = Column(
        Integer,
        nullable=True,
        comment="SEP numeric Status: 2=OK, 1=Canceled, 3=Failed, etc.",
    )
    rrn = Column(
        String(50),
        nullable=True,
        comment="Bank reference number (RRN)",
    )
    trace_no = Column(
        String(50),
        nullable=True,
        comment="SEP trace/tracking number",
    )
    secure_pan = Column(
        String(30),
        nullable=True,
        comment="Masked card number from SEP (e.g., 621986****8080)",
    )
    hashed_card_number = Column(
        String(100),
        nullable=True,
        comment="SHA-256 hashed card number from SEP callback",
    )
    wage = Column(
        BigInteger,
        nullable=True,
        comment="Transaction fee/wage (Rials) — multi-settlement merchants",
    )
    affective_amount = Column(
        BigInteger,
        nullable=True,
        comment="Amount deducted from card — discount system merchants",
    )

    # ── SEP Verify Response Fields ───────────────────────────
    #
    # Populated after calling VerifyTransaction API.
    # verified_amount: Must match self.amount for successful verification
    #   Per SEP docs Section 7:
    #     Case A: verified == expected → success, deliver service
    #     Case B: verified != expected → auto-reverse, don't deliver
    #     Case C: negative result code → error occurred
    #
    verified_amount = Column(
        BigInteger,
        nullable=True,
        comment="Amount returned by VerifyTransaction (must match self.amount)",
    )
    sep_result_code = Column(
        Integer,
        nullable=True,
        comment="ResultCode from Verify/Reverse API (0=success)",
    )
    sep_result_description = Column(
        Text,
        nullable=True,
        comment="ResultDescription from Verify/Reverse API",
    )

    # ── Failure Tracking ─────────────────────────────────────
    failure_reason = Column(
        Text,
        nullable=True,
        comment="Human-readable failure reason for debugging",
    )

    # ── Timestamps ───────────────────────────────────────────
    created_at = Column(
        DateTime(timezone=True),
        default=_utc_now,
        nullable=False,
        comment="When payment was initiated",
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        nullable=False,
        comment="Last status update",
    )
    callback_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="When SEP callback was received",
    )
    verified_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="When VerifyTransaction succeeded",
    )

    # ── Relationships ────────────────────────────────────────
    reverses = relationship(
        "Reverse",
        back_populates="payment",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    discount_code = relationship(
        "DiscountCode",
        back_populates="payments",
        lazy="selectin",
    )
    discount_usage = relationship(
        "DiscountUsage",
        back_populates="payment",
        uselist=False,
        lazy="selectin",
    )
    wallet_transactions = relationship(
        "WalletTransaction",
        back_populates="payment",
        lazy="selectin",
    )

    # ── Indexes ──────────────────────────────────────────────
    __table_args__ = (
        Index("ix_payments_user_status", "user_id", "status"),
        Index("ix_payments_user_created", "user_id", "created_at"),
        Index("ix_payments_status_created", "status", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<Payment(id={self.id}, res_num={self.res_num}, "
            f"amount={self.amount}, status={self.status})>"
        )

    @property
    def is_successful(self) -> bool:
        """Check if payment was successfully verified."""
        return self.status == PaymentStatus.VERIFIED

    @property
    def is_reversible(self) -> bool:
        """Check if payment can be reversed (must be VERIFIED)."""
        return self.status == PaymentStatus.VERIFIED

    @property
    def is_terminal(self) -> bool:
        """Check if payment is in a final state (no more transitions)."""
        return self.status in (
            PaymentStatus.VERIFIED,
            PaymentStatus.FAILED,
            PaymentStatus.REVERSED,
        )
