"""
Admin Schemas for Payment Service

Admin-specific response models that expose more detail than
user-facing schemas (e.g., user_id visible, all SEP fields, stats).
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class AdminPaymentDetailResponse(BaseModel):
    """
    Full payment detail — admin view.

    Exposes all fields including user_id, SEP internal codes,
    failure reasons, and all timestamps.
    """
    id: str
    user_id: str
    res_num: str
    ref_num: Optional[str] = None

    # Amounts
    amount: int
    original_amount: int
    discount_amount: int = 0
    verified_amount: Optional[int] = None

    # Status
    status: str
    state: Optional[str] = None
    status_code: Optional[int] = None

    # SEP fields
    terminal_id: Optional[str] = None
    token: Optional[str] = None
    rrn: Optional[str] = None
    trace_no: Optional[str] = None
    secure_pan: Optional[str] = None
    hashed_card_number: Optional[str] = None
    wage: Optional[int] = None
    affective_amount: Optional[int] = None

    # SEP verify/reverse result
    sep_result_code: Optional[int] = None
    sep_result_description: Optional[str] = None

    # Failure
    failure_reason: Optional[str] = None
    description: Optional[str] = None

    # Discount
    discount_code_id: Optional[str] = None

    # Timestamps
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    callback_at: Optional[datetime] = None
    verified_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AdminPaymentListResponse(BaseModel):
    """Paginated list of payments for admin."""
    total: int
    limit: int
    offset: int
    payments: List[AdminPaymentDetailResponse]


class AdminReverseDetailResponse(BaseModel):
    """Reverse detail — admin view."""
    id: str
    payment_id: str
    ref_num: str
    amount: int
    reason: Optional[str] = None
    status: str
    result_code: Optional[int] = None
    result_description: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AdminReverseListResponse(BaseModel):
    """List of reverses for a payment — admin view."""
    payment_id: str
    payment_status: str
    total: int
    reverses: List[AdminReverseDetailResponse]


class StatusCount(BaseModel):
    """Count of payments in a specific status."""
    status: str
    count: int
    total_amount: int = Field(description="Sum of amounts in Rials")


class AdminOverviewResponse(BaseModel):
    """
    Admin dashboard overview.

    Provides counts by status, total revenue, and alerts
    for payments that need attention.
    """
    total_payments: int
    total_verified_amount: int = Field(description="Total verified revenue in Rials")
    by_status: List[StatusCount]

    # Alerts — payments needing attention
    stuck_callback_received: int = Field(
        description="Payments stuck in CALLBACK_RECEIVED (verify may have failed)"
    )
    stuck_verify_timeout: int = Field(
        description="Payments in VERIFY_TIMEOUT (SEP will auto-reverse in 30 min)"
    )
    amount_mismatches: int = Field(
        description="Payments with AMOUNT_MISMATCH (auto-reversed)"
    )
    recent_failures_24h: int = Field(
        description="Payments failed in last 24 hours"
    )
