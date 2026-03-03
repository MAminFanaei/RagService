"""
Payment Service Exceptions

All payment-specific exceptions extend the app's base AppException
to maintain consistent error handling and response format across
the entire application.

Exception hierarchy:
    AppException (your existing base)
    ├── BadRequestException (400)
    │   ├── InvalidAmountException
    │   ├── PaymentNotReversibleException
    │   ├── ReverseWindowExpiredException
    │   ├── InvalidDiscountException
    │   └── InsufficientBalanceException
    ├── NotFoundException (404)
    │   ├── PaymentNotFoundException
    │   └── WalletNotFoundException
    ├── ConflictException (409)
    │   ├── DoubleSpendException
    │   └── LockAcquisitionException
    └── SEPGatewayException (502) — NEW base
        ├── SEPTokenException
        ├── SEPVerifyException
        ├── SEPReverseException
        └── AmountMismatchException
"""

from fastapi import status
from app.middleware.exceptions import (
    AppException,
    BadRequestException,
    NotFoundException,
    ConflictException,
)


# ─────────────────────────────────────────────────────────────
# Validation Exceptions (400 Bad Request)
# ─────────────────────────────────────────────────────────────

class InvalidAmountException(BadRequestException):
    """
    Payment amount is invalid.
    
    Raised when:
      - Amount is zero, negative, or non-integer
      - Amount is below MIN_PAYMENT_AMOUNT (10,000 Rials)
      - Amount exceeds MAX_PAYMENT_AMOUNT
    """
    error_code = "INVALID_AMOUNT"

    def __init__(
        self,
        message: str = "Invalid payment amount",
        amount: int | None = None,
        min_amount: int | None = None,
        max_amount: int | None = None,
    ):
        self.amount = amount
        self.min_amount = min_amount
        self.max_amount = max_amount
        if amount is not None and min_amount is not None:
            message = (
                f"Amount {amount:,} Rials is out of valid range "
                f"({min_amount:,} - {max_amount:,} Rials)"
            )
        super().__init__(message)


class PaymentNotReversibleException(BadRequestException):
    """
    Payment cannot be reversed.
    
    Raised when:
      - Payment status is not VERIFIED
      - Payment has already been reversed
      - Payment was never successfully completed
    """
    error_code = "PAYMENT_NOT_REVERSIBLE"

    def __init__(
        self,
        message: str = "Payment is not in a reversible state",
        current_status: str | None = None,
    ):
        self.current_status = current_status
        if current_status:
            message = f"Payment with status '{current_status}' cannot be reversed (must be VERIFIED)"
        super().__init__(message)


class ReverseWindowExpiredException(BadRequestException):
    """
    Reverse time window has expired.
    
    SEP allows reverse within 50 minutes of the transaction.
    After that, a different process is needed (manual refund via SEP panel).
    """
    error_code = "REVERSE_WINDOW_EXPIRED"

    def __init__(
        self,
        message: str = "Reverse window has expired",
        minutes_elapsed: int | None = None,
        window_minutes: int = 50,
    ):
        self.minutes_elapsed = minutes_elapsed
        self.window_minutes = window_minutes
        if minutes_elapsed is not None:
            message = (
                f"Reverse window expired: {minutes_elapsed} minutes since transaction "
                f"(maximum: {window_minutes} minutes)"
            )
        super().__init__(message)


class InvalidDiscountException(BadRequestException):
    """
    Discount code is invalid or cannot be applied.
    
    Raised when:
      - Code does not exist or is inactive
      - Code has expired (outside valid_from/valid_until)
      - Code has reached max_uses
      - User has reached per_user_limit for this code
      - Purchase amount is below min_purchase
    """
    error_code = "INVALID_DISCOUNT"

    def __init__(
        self,
        message: str = "Invalid or expired discount code",
        code: str | None = None,
        reason: str | None = None,
    ):
        self.code = code
        self.reason = reason
        if code and reason:
            message = f"Discount code '{code}': {reason}"
        super().__init__(message)


class InsufficientBalanceException(BadRequestException):
    """
    Wallet balance is insufficient for a debit operation.
    
    Raised when trying to debit more than the current wallet balance
    (e.g., during a reverse that debits wallet after reversing payment).
    """
    error_code = "INSUFFICIENT_BALANCE"

    def __init__(
        self,
        message: str = "Insufficient wallet balance",
        current_balance: int | None = None,
        required_amount: int | None = None,
    ):
        self.current_balance = current_balance
        self.required_amount = required_amount
        if current_balance is not None and required_amount is not None:
            message = (
                f"Insufficient balance: have {current_balance:,} Rials, "
                f"need {required_amount:,} Rials"
            )
        super().__init__(message)


# ─────────────────────────────────────────────────────────────
# Not Found Exceptions (404)
# ─────────────────────────────────────────────────────────────

class PaymentNotFoundException(NotFoundException):
    """Payment record not found in database."""
    error_code = "PAYMENT_NOT_FOUND"

    def __init__(self, payment_id: str = "", res_num: str = ""):
        if payment_id:
            message = f"Payment '{payment_id}' not found"
        elif res_num:
            message = f"Payment with ResNum '{res_num}' not found"
        else:
            message = "Payment not found"
        self.payment_id = payment_id
        self.res_num = res_num
        super().__init__(message)


class WalletNotFoundException(NotFoundException):
    """Wallet not found for the specified user."""
    error_code = "WALLET_NOT_FOUND"

    def __init__(self, user_id: str = ""):
        message = f"Wallet for user '{user_id}' not found" if user_id else "Wallet not found"
        self.user_id = user_id
        super().__init__(message)


class DiscountCodeNotFoundException(NotFoundException):
    """Discount code not found."""
    error_code = "DISCOUNT_CODE_NOT_FOUND"

    def __init__(self, code: str = ""):
        message = f"Discount code '{code}' not found" if code else "Discount code not found"
        self.code = code
        super().__init__(message)


# ─────────────────────────────────────────────────────────────
# Conflict Exceptions (409)
# ─────────────────────────────────────────────────────────────

class DoubleSpendException(ConflictException):
    """
    Attempt to use the same RefNum (digital receipt) twice.
    
    This is a critical security check. SEP will verify the same RefNum
    multiple times (per their docs), so WE must prevent double-spending
    on our side.
    
    3-layer protection:
      Layer 1: DB UNIQUE constraint on ref_num column
      Layer 2: Redis lock before processing callback
      Layer 3: Application-level check before calling Verify
    """
    error_code = "DOUBLE_SPEND"

    def __init__(self, ref_num: str = ""):
        message = (
            f"RefNum '{ref_num}' has already been processed"
            if ref_num
            else "Duplicate payment detected"
        )
        self.ref_num = ref_num
        super().__init__(message)


class LockAcquisitionException(ConflictException):
    """
    Could not acquire distributed lock.
    
    Another worker/process is currently handling this transaction.
    The client should NOT retry immediately — the other process
    will complete the operation.
    """
    error_code = "LOCK_ACQUISITION_FAILED"

    def __init__(self, lock_key: str = ""):
        message = (
            f"Transaction '{lock_key}' is being processed by another worker"
            if lock_key
            else "Another process is already handling this transaction"
        )
        self.lock_key = lock_key
        super().__init__(message)


# ─────────────────────────────────────────────────────────────
# SEP Gateway Exceptions (502 Bad Gateway)
# ─────────────────────────────────────────────────────────────

class SEPGatewayException(AppException):
    """
    Base exception for all SEP gateway communication errors.
    
    Used when SEP's API returns an error or is unreachable.
    HTTP 502 because the error originates from an upstream service (SEP).
    """
    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "SEP_GATEWAY_ERROR"

    def __init__(self, message: str = "Payment gateway error"):
        super().__init__(message)


class SEPTokenException(SEPGatewayException):
    """
    Failed to obtain payment token from SEP.
    
    Carries SEP's error code and description for debugging.
    Common causes:
      - Invalid TerminalId
      - IP not whitelisted
      - Invalid parameters
    """
    error_code = "SEP_TOKEN_ERROR"

    def __init__(
        self,
        message: str = "Failed to obtain payment token from SEP",
        sep_error_code: str | None = None,
        sep_error_desc: str | None = None,
    ):
        self.sep_error_code = sep_error_code
        self.sep_error_desc = sep_error_desc
        if sep_error_code and sep_error_desc:
            message = f"{message}: [{sep_error_code}] {sep_error_desc}"
        elif sep_error_code:
            message = f"{message}: error code {sep_error_code}"
        super().__init__(message)


class SEPVerifyException(SEPGatewayException):
    """
    Failed to verify transaction with SEP.
    
    Carries SEP's ResultCode and ResultDescription.
    NOTE: Per SEP docs, if this times out, you MUST retry
    (up to PAYMENT_VERIFY_MAX_RETRIES times within 30 minutes).
    """
    error_code = "SEP_VERIFY_ERROR"

    def __init__(
        self,
        message: str = "Failed to verify transaction with SEP",
        result_code: int | None = None,
        result_description: str | None = None,
    ):
        self.result_code = result_code
        self.result_description = result_description
        if result_code is not None:
            message = f"{message}: [{result_code}] {result_description or 'Unknown error'}"
        super().__init__(message)


class SEPReverseException(SEPGatewayException):
    """
    Failed to reverse transaction with SEP.
    
    Carries SEP's ResultCode and ResultDescription.
    """
    error_code = "SEP_REVERSE_ERROR"

    def __init__(
        self,
        message: str = "Failed to reverse transaction with SEP",
        result_code: int | None = None,
        result_description: str | None = None,
    ):
        self.result_code = result_code
        self.result_description = result_description
        if result_code is not None:
            message = f"{message}: [{result_code}] {result_description or 'Unknown error'}"
        super().__init__(message)


class AmountMismatchException(SEPGatewayException):
    """
    Verified amount doesn't match the requested payment amount.
    
    Per SEP docs (Section 7 — Case B):
    "If the two amounts are not equal, the full amount must be returned
    to the customer's account and the merchant must NOT deliver the service."
    
    When this occurs, the service automatically initiates a reverse.
    """
    error_code = "AMOUNT_MISMATCH"

    def __init__(
        self,
        expected_amount: int,
        verified_amount: int,
    ):
        self.expected_amount = expected_amount
        self.verified_amount = verified_amount
        message = (
            f"Amount mismatch: expected {expected_amount:,} Rials, "
            f"SEP verified {verified_amount:,} Rials. "
            f"Auto-reverse initiated per SEP documentation."
        )
        super().__init__(message)


class SEPTimeoutException(SEPGatewayException):
    """
    SEP API call timed out after all retry attempts.
    
    Per SEP docs: "If VerifyTransaction response doesn't arrive for any reason
    (timeout, network issue, etc.), the merchant should retry a specific number
    of times. Only retry when no response is received, NOT when the result is
    an error (negative)."
    """
    error_code = "SEP_TIMEOUT"

    def __init__(
        self,
        api_name: str = "SEP API",
        attempts: int = 0,
    ):
        self.api_name = api_name
        self.attempts = attempts
        message = f"{api_name} timed out after {attempts} attempt(s)"
        super().__init__(message)


# ─────────────────────────────────────────────────────────────
# Payment Processing Warnings (non-fatal, logged only)
# These extend Exception directly, NOT AppException.
# They are caught and logged but never sent to the client.
# ─────────────────────────────────────────────────────────────

class PaymentWarning(Exception):
    """Base for non-fatal payment issues (logged, not raised to client)."""
    pass


class SEPCallbackWarning(PaymentWarning):
    """Non-critical issue during callback processing (e.g., unexpected extra params)."""
    pass


class MetricsWarning(PaymentWarning):
    """Failed to record metrics but payment processing continues normally."""
    pass


class SEPConnectionException(SEPGatewayException):
    """
    Failed to establish connection to SEP servers.
    Network-level failure — DNS resolution, TCP connect, TLS handshake, etc.
    """
    error_code = "SEP_CONNECTION_ERROR"