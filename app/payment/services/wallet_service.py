"""
Wallet Service

Manages user wallets with a ledger-based approach:
- Every balance change creates a WalletTransaction record
- balance_after field provides an audit trail
- Atomic balance updates via SQL to prevent race conditions

The wallet is created automatically on first use (lazy initialization).
No need to manually create wallets for users.
"""

import uuid
import structlog
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func

from app.payment.models.wallet import Wallet, WalletTransaction
from app.payment.core.constants import WalletTxType
from app.payment.core.metrics import metrics
from app.payment.exceptions import (
    WalletNotFoundException,
    InsufficientBalanceException,
)

logger = structlog.get_logger()


class WalletService:
    """
    Wallet operations — credit, debit, balance queries.
    
    All balance modifications are atomic and create audit records.
    """

    @staticmethod
    async def get_or_create_wallet(
        db: AsyncSession,
        user_id: str,
    ) -> Wallet:
        """
        Get existing wallet or create a new one for the user.
        
        Wallets are created lazily — the first time a user needs one
        (e.g., first payment), it's created with zero balance.
        
        Args:
            db: Database session.
            user_id: The user's ID (matches users.id).
        
        Returns:
            The user's Wallet object.
        """
        query = select(Wallet).where(Wallet.user_id == user_id)
        result = await db.execute(query)
        wallet = result.scalar_one_or_none()
        
        if wallet:
            return wallet
        
        # Create new wallet with zero balance
        wallet = Wallet(
            id=str(uuid.uuid4()),
            user_id=user_id,
            balance=0,
        )
        db.add(wallet)
        await db.flush()  # Get the ID without committing
        
        logger.info(
            "wallet_created",
            wallet_id=wallet.id,
            user_id=user_id,
        )
        
        return wallet

    @staticmethod
    async def credit(
        db: AsyncSession,
        user_id: str,
        amount: int,
        payment_id: Optional[str] = None,
        description: str = "",
    ) -> WalletTransaction:
        """
        Add funds to a user's wallet.
        
        Called after successful payment verification.
        Uses atomic SQL update to prevent race conditions.
        
        Args:
            db: Database session.
            user_id: The user's ID.
            amount: Amount to credit in Rials (must be positive).
            payment_id: Optional payment ID for audit trail.
            description: Human-readable description.
        
        Returns:
            The created WalletTransaction record.
        
        Raises:
            ValueError: If amount is not positive.
        """
        if amount <= 0:
            raise ValueError(f"Credit amount must be positive, got {amount}")
        
        wallet = await WalletService.get_or_create_wallet(db, user_id)
        
        # Atomic balance update — prevents race conditions
        # SQL: UPDATE wallets SET balance = balance + :amount WHERE id = :id
        new_balance = wallet.balance + amount
        await db.execute(
            update(Wallet)
            .where(Wallet.id == wallet.id)
            .values(
                balance=Wallet.balance + amount,
                updated_at=datetime.now(timezone.utc),
            )
        )
        
        # Create transaction record
        tx = WalletTransaction(
            id=str(uuid.uuid4()),
            wallet_id=wallet.id,
            payment_id=payment_id,
            amount=amount,
            balance_after=new_balance,
            tx_type=WalletTxType.CREDIT,
            description=description or f"Credit: {amount:,} Rials",
        )
        db.add(tx)
        await db.flush()
        
        # Update local object
        wallet.balance = new_balance
        
        logger.info(
            "wallet_credited",
            wallet_id=wallet.id,
            user_id=user_id,
            amount=amount,
            new_balance=new_balance,
            payment_id=payment_id,
        )
        
        # Metrics
        metrics.wallet_credited(amount)
        
        return tx

    @staticmethod
    async def debit(
        db: AsyncSession,
        user_id: str,
        amount: int,
        payment_id: Optional[str] = None,
        description: str = "",
    ) -> WalletTransaction:
        """
        Remove funds from a user's wallet.
        
        Called when a verified payment is reversed — we debit back
        the amount that was credited.
        
        Args:
            db: Database session.
            user_id: The user's ID.
            amount: Amount to debit in Rials (must be positive).
            payment_id: Optional payment ID for audit trail.
            description: Human-readable description.
        
        Returns:
            The created WalletTransaction record.
        
        Raises:
            InsufficientBalanceException: If wallet doesn't have enough funds.
            WalletNotFoundException: If user has no wallet.
        """
        if amount <= 0:
            raise ValueError(f"Debit amount must be positive, got {amount}")
        
        wallet = await WalletService.get_or_create_wallet(db, user_id)
        
        if wallet.balance < amount:
            raise InsufficientBalanceException(
                current_balance=wallet.balance,
                required_amount=amount,
            )
        
        # Atomic balance update
        new_balance = wallet.balance - amount
        await db.execute(
            update(Wallet)
            .where(Wallet.id == wallet.id)
            .values(
                balance=Wallet.balance - amount,
                updated_at=datetime.now(timezone.utc),
            )
        )
        
        # Create transaction record
        tx = WalletTransaction(
            id=str(uuid.uuid4()),
            wallet_id=wallet.id,
            payment_id=payment_id,
            amount=-amount,  # Negative for debit
            balance_after=new_balance,
            tx_type=WalletTxType.DEBIT,
            description=description or f"Debit: {amount:,} Rials",
        )
        db.add(tx)
        await db.flush()
        
        wallet.balance = new_balance
        
        logger.info(
            "wallet_debited",
            wallet_id=wallet.id,
            user_id=user_id,
            amount=amount,
            new_balance=new_balance,
            payment_id=payment_id,
        )
        
        metrics.wallet_debited(amount)
        
        return tx

    @staticmethod
    async def get_balance(
        db: AsyncSession,
        user_id: str,
    ) -> dict:
        """
        Get current wallet balance.
        
        Args:
            db: Database session.
            user_id: The user's ID.
        
        Returns:
            dict with wallet_id, balance, user_id, updated_at.
        """
        wallet = await WalletService.get_or_create_wallet(db, user_id)
        
        return {
            "wallet_id": wallet.id,
            "user_id": user_id,
            "balance": wallet.balance,
            "created_at": wallet.created_at.isoformat() if wallet.created_at else None,
            "updated_at": wallet.updated_at.isoformat() if wallet.updated_at else None,
        }

    @staticmethod
    async def get_transactions(
        db: AsyncSession,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        tx_type: Optional[str] = None,
    ) -> dict:
        """
        Get wallet transaction history.
        
        Args:
            db: Database session.
            user_id: The user's ID.
            limit: Max records to return.
            offset: Pagination offset.
            tx_type: Optional filter by CREDIT or DEBIT.
        
        Returns:
            dict with total count and list of transactions.
        """
        wallet = await WalletService.get_or_create_wallet(db, user_id)
        
        query = select(WalletTransaction).where(
            WalletTransaction.wallet_id == wallet.id
        )
        
        if tx_type:
            query = query.where(WalletTransaction.tx_type == tx_type)
        
        # Count total
        count_query = select(func.count(WalletTransaction.id)).where(
            WalletTransaction.wallet_id == wallet.id
        )
        if tx_type:
            count_query = count_query.where(WalletTransaction.tx_type == tx_type)
        
        total_result = await db.execute(count_query)
        total = total_result.scalar()
        
        # Get paginated results
        query = query.order_by(
            WalletTransaction.created_at.desc()
        ).limit(limit).offset(offset)
        
        result = await db.execute(query)
        transactions = result.scalars().all()
        
        return {
            "wallet_id": wallet.id,
            "user_id": user_id,
            "balance": wallet.balance,
            "total": total,
            "limit": limit,
            "offset": offset,
            "transactions": transactions,
        }
