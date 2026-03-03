"""
Reverse Transaction Schemas

SEP Documentation Reference:
- Section 10: "اصلاحیه تراکنش یا Reverse"
- Must be called within 50 minutes of original transaction
- Only verified (confirmed) payments can be reversed
- Uses same TerminalNumber (int) parameter as Verify API
- Response structure is identical to Verify response

Flow:
    1. Client sends ReverseRequest with payment_id and reason
    2. Service validates payment is VERIFIED and within 50-min window
    3. Service calls SEP ReverseTransaction API
    4. Returns ReverseResponse with result
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field

from app.payment.schemas.payment import SEPVerifyInfo


# =============================================================================
# CLIENT-FACING SCHEMAS
# =============================================================================


class ReverseRequest(BaseModel):
    """
    Request to reverse a verified payment.
    
    POST /api/v1/payment/{payment_id}/reverse
    
    Note: Reversal is always for the FULL amount (SEP does not support
    partial reversals). The amount is taken from the original payment.
    
    Example:
        {
            "reason": "Customer requested refund"
        }
    """
    reason: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Reason for reversing this payment"
    )


class ReverseResponse(BaseModel):
    """
    Response after processing a reverse request.
    
    Example (success):
        {
            "reverse_id": "uuid-xxx",
            "payment_id": "uuid-yyy",
            "status": "COMPLETED",
            "amount": 500000,
            "message": "Transaction reversed successfully"
        }
    
    Example (failure):
        {
            "reverse_id": "uuid-xxx",
            "payment_id": "uuid-yyy",
            "status": "FAILED",
            "amount": 500000,
            "message": "Reversal window expired (50 minutes)"
        }
    """
    reverse_id: str
    payment_id: str
    status: str
    amount: int
    result_code: Optional[int] = None
    result_description: Optional[str] = None
    message: str


class ReverseDetailResponse(BaseModel):
    """
    Detailed reverse record for query endpoints.
    """
    reverse_id: str = Field(alias="id")
    payment_id: str
    ref_num: str
    reason: str
    status: str
    result_code: Optional[int] = None
    result_description: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True


class ReverseListResponse(BaseModel):
    """
    List of reverses for a specific payment.
    """
    payment_id: str
    total: int
    reverses: List[ReverseDetailResponse]


# =============================================================================
# SEP API SCHEMAS
# =============================================================================


class SEPReverseRequest(BaseModel):
    """
    Request body for SEP ReverseTransaction API.
    
    POST https://sep.shaparak.ir/verifyTxnRandomSessionkey/ipg/ReverseTransaction
    
    SEP docs say:
    - Same parameter names as Verify: RefNum + TerminalNumber
    - TerminalNumber is Int64 (same note applies — int, not string)
    
    Example:
        {
            "RefNum": "jJnBmy/IojtTemplUH5ke9ULCGtDtb",
            "TerminalNumber": 2015
        }
    """
    RefNum: str = Field(description="Digital receipt of the original transaction")
    TerminalNumber: int = Field(description="Terminal number as integer")


class SEPReverseResponse(BaseModel):
    """
    Response from SEP ReverseTransaction API.
    
    Structure is identical to VerifyTransaction response.
    
    ResultCode values (from SEP docs):
        0    = Success
        2    = Duplicate request
        -105 = Terminal not found
        -104 = Terminal inactive
        -106 = Unauthorized IP
    """
    TransactionDetail: Optional[SEPVerifyInfo] = None
    ResultCode: int
    ResultDescription: Optional[str] = None
    Success: bool

    class Config:
        extra = "allow"
