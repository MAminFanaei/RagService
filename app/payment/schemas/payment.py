"""
Payment Schemas

Covers:
- Client-facing request/response models
- SEP API request/response models (exact field names from docs)
- Query/filter models for listing payments

SEP Documentation Notes:
- Token API: POST to OnlinePG with Action="token"
- Callback: SEP POSTs to RedirectURL with State, RefNum, ResNum, etc.
- Verify API: POST with RefNum + TerminalNumber (note: Number, not Id)
- All amounts are in Rials as integers (no decimals)
- Parameter names are CASE-SENSITIVE (SEP docs section 5, note 4)
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict, field_validator


# =============================================================================
# CLIENT-FACING SCHEMAS (what your frontend sends/receives)
# =============================================================================


class PaymentInitiateRequest(BaseModel):
    """
    Request to start a new payment.
    
    Frontend sends this to POST /api/v1/payment/initiate
    
    Example:
        {
            "amount": 500000,
            "description": "Wallet charge",
            "discount_code": "WELCOME20"
        }
    """
    amount: int = Field(
        ...,
        gt=0,
        description="Amount in Rials. Must be a positive integer."
    )
    description: Optional[str] = Field(
        None,
        max_length=255,
        description="Optional description for this payment"
    )
    cell_number: Optional[str] = Field(
        None,
        pattern=r"^9\d{9}$",
        description=(
            "Buyer mobile number without leading 0 (e.g., 9120000000). "
            "If SEP has saved cards for this number, buyer sees card list."
        )
    )
    discount_code: Optional[str] = Field(
        None,
        max_length=50,
        description="Optional discount code to apply"
    )

    @field_validator("amount")
    @classmethod
    def amount_must_be_positive_integer(cls, v: int) -> int:
        """SEP requires amount as a positive integer, no decimals."""
        if v <= 0:
            raise ValueError("Amount must be greater than 0")
        return v


class PaymentInitiateResponse(BaseModel):
    """
    Response after successfully initiating a payment.
    
    Frontend uses token + redirect_url to send user to SEP.
    
    Example:
        {
            "payment_id": "uuid-xxx",
            "res_num": "PAY-20240101-abc123",
            "token": "2c3c1fefac5a48geb9f9be7e445dd9b2",
            "redirect_url": "https://sep.shaparak.ir/OnlinePG/SendToken?token=xxx",
            "amount": 400000,
            "original_amount": 500000,
            "discount_amount": 100000,
            "discount_code": "WELCOME20"
        }
    """
    payment_id: str
    res_num: str
    token: str
    redirect_url: str
    amount: int = Field(description="Final amount after discount (Rials)")
    original_amount: int = Field(description="Original amount before discount (Rials)")
    discount_amount: int = Field(default=0, description="Discount amount (Rials)")
    discount_code: Optional[str] = None


class PaymentCallbackData(BaseModel):
    """
    Data that SEP POSTs to our RedirectURL after payment.
    
    SEP Documentation Reference:
    - Section 5: "بازگشت به سایت فروشنده"
    - All parameter names are CASE-SENSITIVE
    - State is the string status, Status is the numeric code
    
    Note: This is a DOCUMENTATION schema mirroring SEP's exact format.
    Actual parsing is done by CallbackData in sep_client.py.
    These field names MUST match SEP's exact casing.
    """
    # Required fields — always sent by SEP
    MID: Optional[str] = Field(None, description="Terminal ID (same as TerminalId)")
    State: Optional[str] = Field(None, description="Transaction state string (e.g., 'OK', 'Failed')")
    Status: Optional[int] = Field(None, description="Transaction status numeric code")
    RRN: Optional[str] = Field(None, description="Reference number")
    RefNum: Optional[str] = Field(None, description="Digital receipt — up to 50 chars, UNIQUE per txn")
    ResNum: Optional[str] = Field(None, description="Our reservation number — sent back unchanged")
    TerminalId: Optional[int] = Field(None, description="Terminal ID (numeric)")
    TraceNo: Optional[str] = Field(None, description="Trace number from SEP")
    Amount: Optional[int] = Field(None, description="Transaction amount in Rials")
    Wage: Optional[int] = Field(None, description="Commission amount (for multi-settlement)")
    SecurePan: Optional[str] = Field(None, description="Masked card number (e.g., 621986****8080)")
    HashedCardNumber: Optional[str] = Field(
        None,
        description="SHA256 hashed card number (output is SHA256, NOT MD5)"
    )
    Token: Optional[str] = Field(None, description="Transaction token")
    AffectiveAmount: Optional[int] = Field(
        None,
        description="Amount deducted from card (for discount terminals)"
    )

    model_config = ConfigDict(extra="allow")


class PaymentDetailResponse(BaseModel):
    """
    Detailed payment information returned to the client.
    
    Used for:
    - GET /api/v1/payment/{payment_id}
    - Individual items in payment list
    
    Uses from_attributes=True to auto-populate from Payment model.
    """
    id: str
    res_num: str
    ref_num: Optional[str] = None
    amount: int
    original_amount: int
    discount_amount: int = 0
    status: str
    state: Optional[str] = None
    rrn: Optional[str] = None
    trace_no: Optional[str] = None
    secure_pan: Optional[str] = None
    verified_amount: Optional[int] = None
    failure_reason: Optional[str] = None
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    callback_at: Optional[datetime] = None
    verified_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PaymentListQuery(BaseModel):
    """
    Query parameters for listing payments.
    
    Used with GET /api/v1/payment/list?status=VERIFIED&limit=20&offset=0
    """
    status: Optional[str] = Field(
        None,
        description="Filter by payment status (PENDING, VERIFIED, FAILED, REVERSED)"
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Number of results per page"
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Offset for pagination"
    )
    sort_by: str = Field(
        default="created_at",
        description="Sort field"
    )
    sort_order: str = Field(
        default="desc",
        pattern=r"^(asc|desc)$",
        description="Sort order: asc or desc"
    )
    created_after: Optional[datetime] = Field(
        None,
        description="Filter payments created after this datetime"
    )
    created_before: Optional[datetime] = Field(
        None,
        description="Filter payments created before this datetime"
    )


class PaymentListResponse(BaseModel):
    """
    Paginated list of payments.
    """
    total: int
    limit: int
    offset: int
    payments: List[PaymentDetailResponse]


# =============================================================================
# SEP API SCHEMAS (exact field names from SEP documentation)
# These are documentation schemas matching SEP's API format exactly.
# Actual communication uses dataclasses in sep_client.py.
# =============================================================================


class SEPTokenRequest(BaseModel):
    """
    Request body for SEP Token API.
    
    POST https://sep.shaparak.ir/OnlinePG/OnlinePG
    
    SEP Documentation Reference:
    - Section 2.1: "دریافت توکن"
    - Action must be "token" (case-insensitive per docs)
    - Amount is integer in Rials
    - TerminalId is a string
    - ResNum must be unique per transaction
    
    IMPORTANT: Field names are CASE-SENSITIVE on SEP's side.
    """
    Action: str = Field(default="token", description="Must be 'token'")
    TerminalId: str = Field(description="Merchant terminal ID from SEP")
    Amount: int = Field(description="Purchase amount in Rials (positive integer)")
    ResNum: str = Field(description="Unique reservation/order number from merchant")
    RedirectUrl: str = Field(description="URL to redirect buyer after payment")
    CellNumber: Optional[str] = Field(
        None,
        description="Buyer mobile number for card retrieval (without leading 0)"
    )
    TokenExpiryInMin: Optional[int] = Field(
        None,
        ge=20,
        le=3600,
        description="Token validity in minutes (20-3600, default 20)"
    )

    model_config = ConfigDict(populate_by_name=True)


class SEPTokenResponse(BaseModel):
    """
    Response from SEP Token API.
    
    Success: {"status": 1, "token": "xxx"}
    Error:   {"status": -1, "errorCode": "5", "errorDesc": "..."}
    
    Note: errorCode comes as STRING from SEP, not int.
    """
    status: int = Field(description="1 = success, -1 = error")
    token: Optional[str] = Field(
        None,
        description="Token string — only present when status=1"
    )
    errorCode: Optional[str] = Field(
        None,
        description="Error code as string — only present when status=-1"
    )
    errorDesc: Optional[str] = Field(
        None,
        description="Error description — only present when status=-1"
    )


class SEPVerifyRequest(BaseModel):
    """
    Request body for SEP VerifyTransaction API.
    
    POST https://sep.shaparak.ir/verifyTxnRandomSessionkey/ipg/VerifyTransaction
    
    CRITICAL NOTE from SEP docs:
    - Parameter is 'TerminalNumber' (Int64), NOT 'TerminalId' (String)
    - This is different from the Token API which uses 'TerminalId'
    - RefNum is the digital receipt string
    """
    RefNum: str = Field(description="Digital receipt from callback")
    TerminalNumber: int = Field(
        description=(
            "Terminal number as INTEGER. "
            "Note: Token API uses TerminalId (string), "
            "but Verify uses TerminalNumber (int)."
        )
    )


class SEPVerifyInfo(BaseModel):
    """
    TransactionDetail object inside SEP VerifyTransaction response.
    
    Note: 'OrginalAmount' is SEP's actual spelling (not 'OriginalAmount').
    We preserve their exact spelling for compatibility.
    """
    RRN: Optional[str] = None
    RefNum: Optional[str] = None
    MaskedPan: Optional[str] = Field(
        None,
        description="Masked card number (e.g., 621986****8080)"
    )
    HashedPan: Optional[str] = Field(
        None,
        description="SHA256 hashed card number"
    )
    TerminalNumber: Optional[int] = None
    OrginalAmount: Optional[int] = Field(
        None,
        description="Amount sent to gateway (SEP's spelling, not a typo)"
    )
    AffectiveAmount: Optional[int] = Field(
        None,
        description="Amount actually deducted from card"
    )
    StraceDate: Optional[str] = Field(
        None,
        description="Transaction date string"
    )
    StraceNo: Optional[str] = Field(
        None,
        description="Trace number string"
    )

    model_config = ConfigDict(extra="allow")


class SEPVerifyResponse(BaseModel):
    """
    Response from SEP VerifyTransaction API.
    
    Success example:
        {
            "TransactionDetail": { ... },
            "ResultCode": 0,
            "ResultDescription": "عملیات با موفقیت انجام شد",
            "Success": true
        }
    
    ResultCode values (from SEP docs):
        0  = Success
        -2 = Transaction not found
        -6 = More than 30 minutes passed
        2  = Duplicate request
        5  = Transaction already reversed
        -104 = Terminal inactive
        -105 = Terminal not found
        -106 = Unauthorized IP
    """
    TransactionDetail: Optional[SEPVerifyInfo] = None
    ResultCode: int = Field(description="0 = success, negative = error")
    ResultDescription: Optional[str] = Field(
        None,
        description="Human-readable result description (Persian)"
    )
    Success: bool = Field(description="Overall success flag")

    model_config = ConfigDict(extra="allow")
