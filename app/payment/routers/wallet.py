"""
Wallet Router

GET /wallet/balance — Get current wallet balance.
GET /wallet/transactions — Get wallet transaction history.

Authentication: Required (JWT Bearer token)
Users can only see their own wallet.
Wallets are created automatically on first access.
"""

import structlog
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.payment.schemas.wallet import (
    WalletResponse,
    WalletTransactionResponse,
    WalletTransactionListResponse,
)
from app.payment.services.wallet_service import WalletService

logger = structlog.get_logger()

router = APIRouter()


@router.get(
    "/balance",
    response_model=WalletResponse,
    summary="Get wallet balance",
    description=(
        "Get your current wallet balance. "
        "Wallet is created automatically on first access."
    ),
    responses={
        200: {
            "description": "Current wallet balance",
            "content": {
                "application/json": {
                    "example": {
                        "wallet_id": "uuid-xxx",
                        "user_id": "uuid-yyy",
                        "balance": 1500000,
                        "currency": "IRR",
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-15T12:30:00Z",
                    }
                }
            },
        },
        401: {"description": "Authentication required"},
    },
)
async def get_wallet_balance(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get your wallet balance."""
    result = await WalletService.get_balance(
        db=db,
        user_id=current_user.id,
    )

    return WalletResponse(
        wallet_id=result["wallet_id"],
        user_id=result["user_id"],
        balance=result["balance"],
        currency="IRR",
        created_at=result["created_at"],
        updated_at=result["updated_at"],
    )


@router.get(
    "/transactions",
    response_model=WalletTransactionListResponse,
    summary="Get wallet transaction history",
    description=(
        "Get your wallet transaction history with optional filtering. "
        "Transactions are ordered by most recent first."
    ),
    responses={
        200: {
            "description": "Wallet transaction history",
            "content": {
                "application/json": {
                    "example": {
                        "wallet_id": "uuid-xxx",
                        "user_id": "uuid-yyy",
                        "current_balance": 1500000,
                        "total": 10,
                        "limit": 20,
                        "offset": 0,
                        "transactions": [
                            {
                                "transaction_id": "uuid-zzz",
                                "wallet_id": "uuid-xxx",
                                "payment_id": "uuid-ppp",
                                "amount": 500000,
                                "tx_type": "CREDIT",
                                "balance_after": 1500000,
                                "description": "Wallet charge via SEP",
                                "created_at": "2024-01-15T12:30:00Z",
                            }
                        ],
                    }
                }
            },
        },
        401: {"description": "Authentication required"},
    },
)
async def get_wallet_transactions(
    tx_type: Optional[str] = Query(
        None,
        pattern="^(CREDIT|DEBIT)$",
        description="Filter by type: CREDIT or DEBIT",
    ),
    limit: int = Query(20, ge=1, le=100, description="Results per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get your wallet transaction history."""
    result = await WalletService.get_transactions(
        db=db,
        user_id=current_user.id,
        limit=limit,
        offset=offset,
        tx_type=tx_type,
    )

    # Convert model instances to response schemas
    tx_responses = []
    for tx in result["transactions"]:
        tx_responses.append(
            WalletTransactionResponse(
                transaction_id=tx.id,
                wallet_id=tx.wallet_id,
                payment_id=tx.payment_id,
                amount=tx.amount,
                tx_type=tx.tx_type,
                balance_after=tx.balance_after,
                description=tx.description,
                created_at=tx.created_at,
            )
        )

    return WalletTransactionListResponse(
        wallet_id=result["wallet_id"],
        user_id=result["user_id"],
        current_balance=result["balance"],
        total=result["total"],
        limit=result["limit"],
        offset=result["offset"],
        transactions=tx_responses,
    )
