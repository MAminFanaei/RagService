"""
Wallet Models

Implements a ledger-based wallet system with two tables:

Wallet:
    - One wallet per user (user_id is UNIQUE)
    - Stores current balance in Rials
    - Auto-created when user's first payment is verified
    - Balance is updated atomically via SQL UPDATE ... SET balance = balance + X

WalletTransaction:
    - Immutable ledger of all balance changes
    - Each entry has: amount, type (CREDIT/DEBIT), balance_after (snapshot)
    - Links to the payment that caused the balance change (if any)
    - Provides complete audit trail

Why a separate Wallet table instead of a balance column on User?
    1. Separation of concerns — payment domain doesn't modify user domain
    2. WalletTransaction ledger provides audit trail
    3. Atomic balance updates prevent race conditions
    4. Easy to add features later (multiple wallets, currency support)
    5. Your existing User model stays untouched (zero changes)

Atomic Balance Update Pattern (used in wallet_service.py):
    UPDATE wallets SET balance = balance + :amount WHERE id = :wallet_id
    This is atomic at the database level — no race conditions even
    with concurrent requests hitting different workers.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, BigInteger, DateTime, Enum,
    ForeignKey, Index, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.payment.core.constants import WalletTxType


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Wallet(Base):
    __tablename__ = "wallets"

    # ── Primary Key ──────────────────────────────────────────
    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Wallet UUID",
    )

    # ── User Reference (One Wallet Per User) ─────────────────
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
        comment="FK to users.id — one wallet per user (UNIQUE)",
    )

    # ── Balance ──────────────────────────────────────────────
    #
    # Stored in Rials (smallest unit).
    # Always updated atomically via:
    #   UPDATE wallets SET balance = balance + :amount WHERE id = :id
    #
    # Never set directly in application code — always use
    # wallet_service.credit() or wallet_service.debit()
    #
    balance = Column(
        BigInteger,
        nullable=False,
        default=0,
        comment="Current balance in Rials (updated atomically)",
    )

    # ── Timestamps ───────────────────────────────────────────
    created_at = Column(
        DateTime(timezone=True),
        default=_utc_now,
        nullable=False,
        comment="When wallet was created",
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        nullable=False,
        comment="Last balance change",
    )

    # ── Relationships ────────────────────────────────────────
    transactions = relationship(
        "WalletTransaction",
        back_populates="wallet",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="WalletTransaction.created_at.desc()",
    )

    def __repr__(self) -> str:
        return (
            f"<Wallet(id={self.id}, user_id={self.user_id}, "
            f"balance={self.balance})>"
        )


class WalletTransaction(Base):
    """
    Immutable ledger entry for every wallet balance change.

    Each transaction records:
        - amount: How much changed (always positive in DB, sign from tx_type)
        - tx_type: CREDIT (money in) or DEBIT (money out)
        - balance_after: Wallet balance snapshot AFTER this transaction
        - payment_id: Which payment caused this (NULL for manual adjustments)

    This table is append-only. Rows are never updated or deleted.
    To "undo" a transaction, create a new one with opposite tx_type.

    Examples:
        Payment verified → CREDIT, amount=50000, balance_after=150000
        Reverse completed → DEBIT, amount=50000, balance_after=100000
    """
    __tablename__ = "wallet_transactions"

    # ── Primary Key ──────────────────────────────────────────
    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Transaction UUID",
    )

    # ── Wallet Reference ─────────────────────────────────────
    wallet_id = Column(
        String(36),
        ForeignKey("wallets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="FK to wallets.id",
    )

    # ── Optional Payment Reference ───────────────────────────
    payment_id = Column(
        String(36),
        ForeignKey("payments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="FK to payments.id — which payment caused this (NULL for manual)",
    )

    # ── Transaction Details ──────────────────────────────────
    amount = Column(
        BigInteger,
        nullable=False,
        comment="Transaction amount in Rials (always positive)",
    )
    tx_type = Column(
        Enum(WalletTxType, native_enum=False, length=10),
        nullable=False,
        comment="CREDIT (money in) or DEBIT (money out)",
    )
    balance_after = Column(
        BigInteger,
        nullable=False,
        comment="Wallet balance snapshot after this transaction",
    )
    description = Column(
        String(255),
        nullable=True,
        comment="Human-readable description (e.g., 'Wallet charge via SEP')",
    )

    # ── Timestamp ────────────────────────────────────────────
    created_at = Column(
        DateTime(timezone=True),
        default=_utc_now,
        nullable=False,
        comment="When this transaction occurred (immutable)",
    )

    # ── Relationships ────────────────────────────────────────
    wallet = relationship(
        "Wallet",
        back_populates="transactions",
    )
    payment = relationship(
        "Payment",
        back_populates="wallet_transactions",
    )

    # ── Indexes ──────────────────────────────────────────────
    __table_args__ = (
        Index("ix_wallet_tx_wallet_created", "wallet_id", "created_at"),
        Index("ix_wallet_tx_type_created", "tx_type", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<WalletTransaction(id={self.id}, type={self.tx_type}, "
            f"amount={self.amount}, balance_after={self.balance_after})>"
        )
