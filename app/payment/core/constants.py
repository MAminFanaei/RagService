"""
Payment Constants & SEP Status Codes

All enums, SEP gateway codes, and business rule constants.
Every value is sourced directly from the SEP (Saman Electronic Payment)
documentation to ensure exact compatibility.

IMPORTANT — SEP Quirks Documented Here:
  1. Token API uses "TerminalId" (string), but Verify/Reverse use "TerminalNumber" (int)
  2. SEP has a typo: "OrginalAmount" instead of "OriginalAmount" — we must use their spelling
  3. Parameter names are CASE-SENSITIVE
  4. State is English string, Status is numeric — both sent in callback
  5. Token API response uses status=1 (success) and status=-1 (failure)
"""

import enum


# ═════════════════════════════════════════════════════════════
# INTERNAL ENUMS (Our payment system's state machine)
# ═════════════════════════════════════════════════════════════

class PaymentStatus(str, enum.Enum):
    """
    Payment lifecycle states.

    Flow:
        PENDING → TOKEN_OBTAINED → CALLBACK_RECEIVED → VERIFIED
                                                     ↘ FAILED
                                                     ↘ AMOUNT_MISMATCH (auto-reverse)
                                                     ↘ VERIFY_TIMEOUT (SEP auto-reverses in 30 min)
        VERIFIED → REVERSED
    """
    PENDING = "PENDING"
    TOKEN_OBTAINED = "TOKEN_OBTAINED"
    CALLBACK_RECEIVED = "CALLBACK_RECEIVED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    VERIFY_TIMEOUT = "VERIFY_TIMEOUT"
    REVERSED = "REVERSED"


class ReverseStatus(str, enum.Enum):
    """Reverse operation lifecycle states."""
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class WalletTxType(str, enum.Enum):
    """Wallet transaction types."""
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"


class DiscountType(str, enum.Enum):
    """Discount code types."""
    PERCENTAGE = "PERCENTAGE"
    FIXED = "FIXED"


# ═════════════════════════════════════════════════════════════
# SEP CALLBACK STATUS CODES
# Sent as 'State' (string) and 'Status' (int) in callback POST
# Source: SEP Documentation — "جدول وضعیت تراکنش"
# ═════════════════════════════════════════════════════════════

class SEPState(str, enum.Enum):
    """
    SEP callback State values (English strings).

    Only SEPState.OK indicates a successful payment.
    All other states mean the payment did not complete.
    """
    OK = "OK"
    CANCELED_BY_USER = "CanceledByUser"
    FAILED = "Failed"
    SESSION_IS_NULL = "SessionIsNull"
    INVALID_PARAMETERS = "InvalidParameters"
    MERCHANT_IP_INVALID = "MerchantIpAddressIsInvalid"
    TOKEN_NOT_FOUND = "TokenNotFound"
    TOKEN_REQUIRED = "TokenRequired"
    TERMINAL_NOT_FOUND = "TerminalNotFound"
    MULTISETTLE_POLICY_ERRORS = "MultisettlePolicyErrors"


# Numeric status codes sent alongside State in SEP callback
# Key = Status code, Value = (State string, Persian description)
SEP_CALLBACK_STATUS_CODES: dict[int, dict[str, str]] = {
    1: {
        "state": "CanceledByUser",
        "description_fa": "کاربر انصراف داده است",
        "description_en": "User canceled the payment",
    },
    2: {
        "state": "OK",
        "description_fa": "پرداخت با موفقیت انجام شد",
        "description_en": "Payment completed successfully",
    },
    3: {
        "state": "Failed",
        "description_fa": "پرداخت انجام نشد",
        "description_en": "Payment failed",
    },
    4: {
        "state": "SessionIsNull",
        "description_fa": "کاربر در بازه زمانی تعیین شده پاسخی ارسال نکرده است",
        "description_en": "User did not respond within the allowed time",
    },
    5: {
        "state": "InvalidParameters",
        "description_fa": "پارامترهای ارسالی نامعتبر است",
        "description_en": "Invalid parameters sent",
    },
    8: {
        "state": "MerchantIpAddressIsInvalid",
        "description_fa": "آدرس سرور پذیرنده نامعتبر است (در پرداخت‌های بر پایه توکن)",
        "description_en": "Merchant server IP address is invalid (token-based payments)",
    },
    10: {
        "state": "TokenNotFound",
        "description_fa": "توکن ارسال شده یافت نشد",
        "description_en": "Token not found",
    },
    11: {
        "state": "TokenRequired",
        "description_fa": "با این شماره ترمینال فقط تراکنش‌های توکنی قابل پرداخت هستند",
        "description_en": "This terminal only accepts token-based transactions",
    },
    12: {
        "state": "TerminalNotFound",
        "description_fa": "شماره ترمینال ارسال شده یافت نشد",
        "description_en": "Terminal number not found",
    },
    21: {
        "state": "MultisettlePolicyErrors",
        "description_fa": "محدودیت‌های مدل چند حسابی رعایت نشده",
        "description_en": "Multi-settlement policy errors",
    },
}

# The ONLY callback status code that means payment success
SEP_SUCCESS_STATUS: int = 2


# ═════════════════════════════════════════════════════════════
# SEP TOKEN API RESPONSE CODES
# Source: SEP Documentation — "پارامترهای پاسخ درخواست توکن"
# ═════════════════════════════════════════════════════════════

SEP_TOKEN_SUCCESS: int = 1    # status=1 → token field contains the token
SEP_TOKEN_FAILURE: int = -1   # status=-1 → errorCode and errorDesc fields present

# Token error codes reuse the callback status codes for values 5+
# Additional token-specific error context is in errorDesc field


# ═════════════════════════════════════════════════════════════
# SEP VERIFY & REVERSE RESULT CODES
# Source: SEP Documentation — "کدهای پاسخ سرویس های تایید و اصلاح تراکنش"
# ═════════════════════════════════════════════════════════════

class SEPResultCode:
    """
    Result codes from SEP VerifyTransaction and ReverseTransaction APIs.

    Usage:
        if result_code == SEPResultCode.SUCCESS:
            # Transaction verified
        elif result_code == SEPResultCode.TRANSACTION_NOT_FOUND:
            # Transaction not found in SEP
    """
    SUCCESS: int = 0
    DUPLICATE_REQUEST: int = 2
    TRANSACTION_ALREADY_REVERSED: int = 5       # Verify only
    TRANSACTION_NOT_FOUND: int = -2             # Verify only
    VERIFY_WINDOW_EXPIRED: int = -6             # Verify only: >30 min
    TERMINAL_INACTIVE: int = -104               # Both
    TERMINAL_NOT_FOUND: int = -105              # Both
    IP_NOT_ALLOWED: int = -106                  # Both


# Verify result codes with bilingual descriptions
SEP_VERIFY_RESULT_CODES: dict[int, dict[str, str]] = {
    -2: {
        "description_fa": "تراکنش یافت نشد",
        "description_en": "Transaction not found",
        "api": "verify",
    },
    -6: {
        "description_fa": "بیش از نیم ساعت از زمان اجرای تراکنش گذشته است",
        "description_en": "More than 30 minutes since transaction execution",
        "api": "verify",
    },
    0: {
        "description_fa": "موفق",
        "description_en": "Success",
        "api": "verify|reverse",
    },
    2: {
        "description_fa": "درخواست تکراری می باشد",
        "description_en": "Duplicate request",
        "api": "verify|reverse",
    },
    -105: {
        "description_fa": "ترمینال ارسالی در سیستم موجود نمی باشد",
        "description_en": "Terminal not found in system",
        "api": "verify|reverse",
    },
    -104: {
        "description_fa": "ترمینال ارسالی غیرفعال می باشد",
        "description_en": "Terminal is inactive",
        "api": "verify|reverse",
    },
    -106: {
        "description_fa": "آدرس آی پی درخواستی غیر مجاز می باشد",
        "description_en": "IP address is not allowed",
        "api": "verify|reverse",
    },
    5: {
        "description_fa": "تراکنش برگشت خورده می باشد",
        "description_en": "Transaction has already been reversed",
        "api": "verify",
    },
}

# Reverse-specific result codes (subset of verify codes)
SEP_REVERSE_RESULT_CODES: dict[int, dict[str, str]] = {
    code: info
    for code, info in SEP_VERIFY_RESULT_CODES.items()
    if "reverse" in info["api"]
}


# ═════════════════════════════════════════════════════════════
# SEP API PARAMETER NAMES (Case-Sensitive!)
# Documenting the exact parameter names SEP expects
# ═════════════════════════════════════════════════════════════

class SEPParams:
    """
    Exact parameter names used by SEP APIs.

    WARNING: SEP is case-sensitive. Use these constants exactly.

    Note the inconsistency between APIs:
    - Token API: TerminalId (string)
    - Verify/Reverse API: TerminalNumber (int)
    This is documented in SEP's own docs and is NOT a bug.
    """
    # ── Token Request ──
    ACTION = "action"
    ACTION_VALUE_TOKEN = "token"
    TERMINAL_ID = "TerminalId"
    AMOUNT = "Amount"
    RES_NUM = "ResNum"
    REDIRECT_URL = "RedirectUrl"
    CELL_NUMBER = "CellNumber"
    TOKEN_EXPIRY = "TokenExpiryInMin"
    WAGE = "Wage"

    # ── Token Response ──
    STATUS = "status"
    TOKEN = "token"
    ERROR_CODE = "errorCode"
    ERROR_DESC = "errorDesc"

    # ── Callback POST params (from SEP to our callback URL) ──
    CB_MID = "MID"
    CB_STATE = "State"
    CB_STATUS = "Status"
    CB_RRN = "RRN"
    CB_REF_NUM = "RefNum"
    CB_RES_NUM = "ResNum"
    CB_TERMINAL_ID = "TerminalId"
    CB_TRACE_NO = "TraceNo"
    CB_AMOUNT = "Amount"
    CB_WAGE = "Wage"
    CB_SECURE_PAN = "SecurePan"
    CB_HASHED_CARD = "HashedCardNumber"
    CB_AFFECTIVE_AMOUNT = "AffectiveAmount"
    CB_TOKEN = "Token"

    # ── Verify/Reverse Request ──
    # NOTE: TerminalNumber (int), NOT TerminalId (string)
    VR_REF_NUM = "RefNum"
    VR_TERMINAL_NUMBER = "TerminalNumber"

    # ── Verify/Reverse Response ──
    VR_TRANSACTION_DETAIL = "TransactionDetail"
    VR_RESULT_CODE = "ResultCode"
    VR_RESULT_DESCRIPTION = "ResultDescription"
    VR_SUCCESS = "Success"

    # ── VerifyInfo (inside TransactionDetail) ──
    VI_RRN = "RRN"
    VI_REF_NUM = "RefNum"
    VI_MASKED_PAN = "MaskedPan"
    VI_HASHED_PAN = "HashedPan"
    VI_TERMINAL_NUMBER = "TerminalNumber"
    VI_ORGINAL_AMOUNT = "OrginalAmount"       # SEP's typo — NOT "OriginalAmount"
    VI_AFFECTIVE_AMOUNT = "AffectiveAmount"
    VI_STRACE_DATE = "StraceDate"
    VI_STRACE_NO = "StraceNo"


# ═════════════════════════════════════════════════════════════
# REDIS LOCK KEY PREFIXES
# ═════════════════════════════════════════════════════════════

class LockPrefix:
    """Redis distributed lock key prefixes."""
    PAYMENT_CALLBACK = "payment:callback:"
    PAYMENT_REFNUM = "payment:ref:"
    REVERSE = "reverse:"
    WALLET = "wallet:"
    DISCOUNT = "discount:"


# ═════════════════════════════════════════════════════════════
# PROMETHEUS METRIC NAMES (centralized — used by core/metrics.py)
# ═════════════════════════════════════════════════════════════

METRIC_PAYMENT_INITIATED = "payment_initiated_total"
METRIC_PAYMENT_VERIFIED = "payment_verified_total"
METRIC_PAYMENT_FAILED = "payment_failed_total"
METRIC_PAYMENT_REVERSED = "payment_reversed_total"
METRIC_DISCOUNT_USED = "discount_used_total"
METRIC_DOUBLE_SPEND_BLOCKED = "payment_double_spend_blocked_total"
METRIC_PAYMENT_DURATION = "payment_duration_seconds"
METRIC_SEP_API_DURATION = "sep_api_duration_seconds"
METRIC_PAYMENT_AMOUNT = "payment_amount_rials"
METRIC_ACTIVE_PAYMENTS = "payment_active_processing"
METRIC_TOKEN_OBTAINED = "payment_token_obtained_total"
METRIC_TOKEN_FAILED = "payment_token_failed_total"
METRIC_VERIFY_FAILED = "payment_verify_failed_total"
METRIC_REVERSE_COMPLETED = "payment_reverse_completed_total"
METRIC_REVERSE_FAILED = "payment_reverse_failed_total"
METRIC_WALLET_CREDITED = "wallet_credited_total"
METRIC_WALLET_DEBITED = "wallet_debited_total"
METRIC_SEP_API_CALLS = "sep_api_calls_total"


# ═════════════════════════════════════════════════════════════
# BUSINESS RULES
# ═════════════════════════════════════════════════════════════

# ResNum generation prefix (helps identify our transactions in SEP reports)
RES_NUM_PREFIX: str = "PAY"

# Currency
CURRENCY: str = "IRR"  # ISO 4217: Iranian Rial

# Payment amount limits (in Rials) — defaults, overridable via config.py / env vars
MIN_PAYMENT_AMOUNT: int = 10_000             # 1,000 Tomans minimum
MAX_PAYMENT_AMOUNT: int = 500_000_000        # 50,000,000 Tomans maximum

# SEP time windows (enforced by SEP, not configurable by merchant)
SEP_VERIFY_WINDOW_MINUTES: int = 30    # Must verify within 30 min or SEP auto-reverses
SEP_REVERSE_WINDOW_MINUTES: int = 50   # Can reverse within 50 min of transaction

# SEP field length limits (from PSP documentation)
MAX_REF_NUM_LENGTH: int = 50           # RefNum max length per SEP docs
MAX_REDIRECT_URL_LENGTH: int = 2083    # RedirectURL max (1538 with GetMethod)
