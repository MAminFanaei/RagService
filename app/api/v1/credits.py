"""
Credits API — purchase messages and get pricing info.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.services.credit_service import CreditService
from app.payment.services.wallet_service import WalletService
from app.middleware.exceptions import BadRequestException, PaymentRequiredException
from app.config import settings

router = APIRouter(prefix="/credits", tags=["Credits"])


class PurchaseRequest(BaseModel):
    message_count: int = Field(..., ge=1, description="Number of messages to buy")


class PurchaseResponse(BaseModel):
    purchased: int
    amount_charged: int
    remaining: int
    wallet_tx_id: str


class PricingResponse(BaseModel):
    price_per_message: int
    free_messages_for_new_users: int
    min_purchase: int
    max_purchase: int
    currency: str = "IRR"


@router.get("/pricing", response_model=PricingResponse)
async def get_pricing():
    """Public endpoint — returns pricing info for frontend display."""
    return PricingResponse(
        price_per_message=settings.PRICE_PER_MESSAGE,
        free_messages_for_new_users=settings.FREE_MESSAGES_FOR_NEW_USERS,
        min_purchase=settings.MIN_MESSAGE_PURCHASE,
        max_purchase=settings.MAX_MESSAGE_PURCHASE,
    )


@router.post("/purchase", response_model=PurchaseResponse)
async def purchase_credits(
    body: PurchaseRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Buy messages by debiting wallet balance."""
    if body.message_count < settings.MIN_MESSAGE_PURCHASE:
        raise BadRequestException(
            f"Minimum purchase is {settings.MIN_MESSAGE_PURCHASE} messages."
        )
    if body.message_count > settings.MAX_MESSAGE_PURCHASE:
        raise BadRequestException(
            f"Maximum purchase is {settings.MAX_MESSAGE_PURCHASE} messages."
        )

    total_price = body.message_count * settings.PRICE_PER_MESSAGE

    # Check wallet balance BEFORE attempting debit (for rich error)
    wallet = await WalletService.get_or_create_wallet(db, current_user.id)
    if wallet.balance < total_price:
        raise PaymentRequiredException(
            message="Not enough wallet balance.",
            data={
                "error_code": "INSUFFICIENT_WALLET_BALANCE",
                "wallet_balance": wallet.balance,
                "required_amount": total_price,
                "shortfall": total_price - wallet.balance,
                "action": "charge_wallet",
            },
        )

    result = await CreditService.purchase(
        db=db,
        user_id=current_user.id,
        message_count=body.message_count,
    )
    await db.commit()

    return PurchaseResponse(**result)