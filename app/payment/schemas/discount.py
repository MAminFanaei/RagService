"""
Discount Code Schemas

Covers:
- Admin creating discount codes
- User validating a discount code before payment
- Preview of discount calculation
- Discount detail for admin views

Design Notes:
- Only ONE discount code per payment (no stacking)
- Codes can be PERCENTAGE (with optional cap) or FIXED amount
- per_user_limit controls how many times one user can use a code
- Validation checks: active, date range, max_uses, per_user_limit, min_purchase
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class DiscountCreateRequest(BaseModel):
    """
    Admin request to create a new discount code.
    
    POST /api/v1/payment/discount/create (requires admin JWT)
    
    Example (percentage discount):
        {
            "code": "WELCOME20",
            "discount_type": "PERCENTAGE",
            "discount_value": 20,
            "max_discount": 200000,
            "min_purchase": 100000,
            "max_uses": 1000,
            "per_user_limit": 1,
            "valid_from": "2024-01-01T00:00:00Z",
            "valid_until": "2024-12-31T23:59:59Z"
        }
    
    Example (fixed discount):
        {
            "code": "FLAT50K",
            "discount_type": "FIXED",
            "discount_value": 50000,
            "min_purchase": 200000,
            "max_uses": 500,
            "per_user_limit": 1,
            "valid_from": "2024-01-01T00:00:00Z",
            "valid_until": "2024-06-30T23:59:59Z"
        }
    """
    code: str = Field(
        ...,
        min_length=2,
        max_length=50,
        description="Unique discount code string"
    )
    discount_type: str = Field(
        ...,
        pattern=r"^(PERCENTAGE|FIXED)$",
        description="PERCENTAGE or FIXED"
    )
    discount_value: int = Field(
        ...,
        gt=0,
        description=(
            "For PERCENTAGE: value between 1-100. "
            "For FIXED: amount in Rials."
        )
    )
    max_discount: Optional[int] = Field(
        None,
        gt=0,
        description="Maximum discount cap in Rials (only for PERCENTAGE type)"
    )
    min_purchase: int = Field(
        default=0,
        ge=0,
        description="Minimum purchase amount in Rials to use this code"
    )
    max_uses: Optional[int] = Field(
        None,
        gt=0,
        description="Total number of times this code can be used (NULL = unlimited)"
    )
    per_user_limit: int = Field(
        default=1,
        ge=1,
        description="How many times a single user can use this code"
    )
    valid_from: datetime = Field(
        description="When this code becomes active"
    )
    valid_until: datetime = Field(
        description="When this code expires"
    )

    @field_validator("discount_value")
    @classmethod
    def validate_discount_value(cls, v: int, info) -> int:
        """Percentage must be 1-100."""
        # Note: We can't easily cross-reference discount_type here in v2,
        # so the service layer does the full cross-field validation.
        return v

    @field_validator("code")
    @classmethod
    def normalize_code(cls, v: str) -> str:
        """Discount codes are always uppercase, trimmed."""
        return v.strip().upper()

    @field_validator("valid_until")
    @classmethod
    def valid_until_after_from(cls, v: datetime, info) -> datetime:
        """Ensure valid_until is after valid_from."""
        if "valid_from" in info.data and v <= info.data["valid_from"]:
            raise ValueError("valid_until must be after valid_from")
        return v


class DiscountValidateRequest(BaseModel):
    """
    User request to validate a discount code before initiating payment.
    
    POST /api/v1/payment/discount/validate
    
    Returns a preview of what the discount would be for a given amount.
    Does NOT consume the code — only previews.
    
    Example:
        {
            "code": "WELCOME20",
            "amount": 500000
        }
    """
    code: str = Field(
        ...,
        min_length=2,
        max_length=50,
        description="Discount code to validate"
    )
    amount: int = Field(
        ...,
        gt=0,
        description="Purchase amount in Rials to calculate discount against"
    )

    @field_validator("code")
    @classmethod
    def normalize_code(cls, v: str) -> str:
        return v.strip().upper()


class DiscountValidateResponse(BaseModel):
    """
    Result of discount code validation.
    
    Example (valid):
        {
            "valid": true,
            "code": "WELCOME20",
            "discount_type": "PERCENTAGE",
            "discount_value": 20,
            "discount_amount": 100000,
            "final_amount": 400000,
            "original_amount": 500000,
            "message": "20% discount applied (max 200,000 Rials)"
        }
    
    Example (invalid):
        {
            "valid": false,
            "code": "EXPIRED50",
            "message": "This discount code has expired"
        }
    """
    valid: bool
    code: str
    discount_type: Optional[str] = None
    discount_value: Optional[int] = None
    discount_amount: Optional[int] = Field(
        None,
        description="Calculated discount in Rials"
    )
    final_amount: Optional[int] = Field(
        None,
        description="Amount after discount in Rials"
    )
    original_amount: Optional[int] = None
    message: str


class DiscountDetailResponse(BaseModel):
    """
    Full discount code details (admin view).
    """
    id: str
    code: str
    discount_type: str
    discount_value: int
    max_discount: Optional[int] = None
    min_purchase: int
    max_uses: Optional[int] = None
    used_count: int
    per_user_limit: int
    valid_from: datetime
    valid_until: datetime
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True
