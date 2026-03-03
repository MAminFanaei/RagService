"""
Payment Schemas Package

Pydantic models for request validation, response serialization,
and data transfer between layers.

All schemas follow these conventions:
- Request schemas: *Request suffix (what the client sends)
- Response schemas: *Response suffix (what we return)
- Internal schemas: *Info suffix (internal data transfer)
- SEP schemas: SEP* prefix (matches SEP API exactly)

Usage:
    from app.payment.schemas import (
        PaymentInitiateRequest,
        PaymentInitiateResponse,
        PaymentDetailResponse,
    )
"""

# Payment schemas
from app.payment.schemas.payment import (
    PaymentInitiateRequest,
    PaymentInitiateResponse,
    PaymentCallbackData,
    PaymentDetailResponse,
    PaymentListResponse,
    PaymentListQuery,
    SEPTokenRequest,
    SEPTokenResponse,
    SEPVerifyRequest,
    SEPVerifyResponse,
    SEPVerifyInfo,
)

# Reverse schemas
from app.payment.schemas.reverse import (
    ReverseRequest,
    ReverseResponse,
    ReverseDetailResponse,
    ReverseListResponse,
    SEPReverseRequest,
    SEPReverseResponse,
)

# Wallet schemas
from app.payment.schemas.wallet import (
    WalletResponse,
    WalletTransactionResponse,
    WalletTransactionListResponse,
    WalletTransactionQuery,
)

# Discount schemas
from app.payment.schemas.discount import (
    DiscountCreateRequest,
    DiscountValidateRequest,
    DiscountValidateResponse,
    DiscountDetailResponse,
)

__all__ = [
    # Payment
    "PaymentInitiateRequest",
    "PaymentInitiateResponse",
    "PaymentCallbackData",
    "PaymentDetailResponse",
    "PaymentListResponse",
    "PaymentListQuery",
    "SEPTokenRequest",
    "SEPTokenResponse",
    "SEPVerifyRequest",
    "SEPVerifyResponse",
    "SEPVerifyInfo",
    # Reverse
    "ReverseRequest",
    "ReverseResponse",
    "ReverseDetailResponse",
    "ReverseListResponse",
    "SEPReverseRequest",
    "SEPReverseResponse",
    # Wallet
    "WalletResponse",
    "WalletTransactionResponse",
    "WalletTransactionListResponse",
    "WalletTransactionQuery",
    # Discount
    "DiscountCreateRequest",
    "DiscountValidateRequest",
    "DiscountValidateResponse",
    "DiscountDetailResponse",
]
