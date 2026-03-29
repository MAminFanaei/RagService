"""
Wallet Schemas

Covers:
- Wallet balance response
- Wallet transaction history (credits/debits)
- Query filters for transaction listing

Design Notes:
- Wallets are created automatically on first payment
- Balance is always in Rials
- Transactions form an append-only ledger
- balance_after provides running balance snapshot
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field

from app.payment.core.constants import CURRENCY


class WalletResponse(BaseModel):
    """
    Current wallet state for a user.
    
    GET /api/v1/payment/wallet/balance
    
    Example:
        {
            "wallet_id": "uuid-xxx",
            "user_id": "uuid-yyy",
            "balance": 1500000,
            "currency": "IRR",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-15T12:30:00Z"
        }
    """
    wallet_id: str
    user_id: str
    balance: int = Field(description="Current balance in Rials")
    currency: str = Field(default=CURRENCY, description="Currency code (always IRR)")
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WalletTransactionResponse(BaseModel):
    """
    A single wallet transaction record.
    
    Example (credit from payment):
        {
            "transaction_id": "uuid-xxx",
            "wallet_id": "uuid-yyy",
            "payment_id": "uuid-zzz",
            "amount": 500000,
            "tx_type": "CREDIT",
            "balance_after": 1500000,
            "description": "Wallet charge via SEP payment",
            "created_at": "2024-01-15T12:30:00Z"
        }
    """
    transaction_id: str
    wallet_id: str
    payment_id: Optional[str] = None
    amount: int = Field(description="Positive for credit, value for debit")
    tx_type: str = Field(description="CREDIT or DEBIT")
    balance_after: int = Field(description="Wallet balance after this transaction")
    description: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class WalletTransactionQuery(BaseModel):
    """
    Query parameters for wallet transaction history.
    
    GET /api/v1/payment/wallet/transactions?tx_type=CREDIT&limit=20
    """
    tx_type: Optional[str] = Field(
        None,
        pattern=r"^(CREDIT|DEBIT)$",
        description="Filter by transaction type"
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Results per page"
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Pagination offset"
    )
    created_after: Optional[datetime] = Field(
        None,
        description="Filter transactions after this datetime"
    )
    created_before: Optional[datetime] = Field(
        None,
        description="Filter transactions before this datetime"
    )


class WalletTransactionListResponse(BaseModel):
    """
    Paginated wallet transaction history.
    """
    wallet_id: str
    user_id: str
    current_balance: int
    total: int
    limit: int
    offset: int
    transactions: List[WalletTransactionResponse]
