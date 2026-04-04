from pydantic import BaseModel , Field
from app.config import Settings

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