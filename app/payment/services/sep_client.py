"""
Saman Electronic Payment (SEP) Gateway Client.

HTTP client for communicating with SEP's REST APIs:
1. Token API — Request a unique payment token
2. Verify API — Verify/confirm a completed transaction
3. Reverse API — Reverse a verified transaction (full refund)

IMPORTANT — SEP API Quirks (from official docs):
- Token API uses 'TerminalId' (string) but Verify/Reverse use 'TerminalNumber' (int64)
- Token API field 'action' must be 'token' (case-insensitive per docs, but we send lowercase)
- Parameter names ARE case-sensitive (نکته 4 از مستندات)
- Token response: status=1 means success, status=-1 means error
- Verify response: Success=true/false, ResultCode=0 means success
- Verify returns 'OrginalAmount' (NOT 'OriginalAmount') — this is SEP's typo, preserved as-is
- Each token is valid for 20 minutes by default (configurable via TokenExpiryInMin: 20-3600)
- Verify must be called within 30 minutes or transaction auto-reverses
- Reverse can be called within 50 minutes of transaction time

Usage:
    from app.payment.services.sep_client import sep_client

    # Get token
    result = await sep_client.request_token(
        amount=500000,
        res_num="ORDER-001",
        redirect_url="https://mysite.com/api/v1/payment/callback",
        cell_number="9120000000",  # optional
    )

    # Verify transaction
    result = await sep_client.verify_transaction(ref_num="abc123")

    # Reverse transaction
    result = await sep_client.reverse_transaction(ref_num="abc123")
"""

import httpx
import structlog
from typing import Optional, Any
from dataclasses import dataclass, field

from app.payment.config import payment_settings
from app.payment.core.constants import (
    SEPParams,
    SEP_RESULT_CODES,
    SEP_CALLBACK_STATUS_CODES,
)
from app.payment.core.metrics import metrics, track_time, duration_since
from app.payment.exceptions import (
    SEPGatewayException,
    SEPTokenException,
    SEPVerifyException,
    SEPReverseException,
    SEPTimeoutException,
    SEPConnectionException,
)

logger = structlog.get_logger()


# ─────────────────────────────────────────────────────────────
# Response Data Classes
# ─────────────────────────────────────────────────────────────


@dataclass
class TokenResponse:
    """
    Response from SEP Token API.

    On success: status=1, token="xxx"
    On failure: status=-1, error_code="5", error_desc="..."
    """

    success: bool
    status: int  # 1 = success, -1 = error
    token: Optional[str] = None
    error_code: Optional[str] = None
    error_desc: Optional[str] = None
    raw_response: dict = field(default_factory=dict)

    @classmethod
    def from_sep_response(cls, data: dict) -> "TokenResponse":
        """Parse SEP's JSON response into TokenResponse."""
        status = data.get("status", -1)
        return cls(
            success=status == 1,
            status=status,
            token=data.get("token"),
            error_code=data.get("errorCode"),
            error_desc=data.get("errorDesc"),
            raw_response=data,
        )


@dataclass
class VerifyTransactionDetail:
    """
    Transaction detail from SEP Verify response.

    Maps to 'TransactionDetail' object in SEP docs.
    Note: 'OrginalAmount' is SEP's actual field name (their typo).
    """

    rrn: Optional[str] = None
    ref_num: Optional[str] = None
    masked_pan: Optional[str] = None
    hashed_pan: Optional[str] = None
    terminal_number: Optional[int] = None
    original_amount: Optional[int] = None  # SEP calls this 'OrginalAmount'
    affective_amount: Optional[int] = None
    strace_date: Optional[str] = None
    strace_no: Optional[str] = None

    @classmethod
    def from_sep_response(cls, data: Optional[dict]) -> Optional["VerifyTransactionDetail"]:
        """Parse SEP's TransactionDetail object."""
        if not data:
            return None
        return cls(
            rrn=data.get("RRN"),
            ref_num=data.get("RefNum"),
            masked_pan=data.get("MaskedPan"),
            hashed_pan=data.get("HashedPan"),
            terminal_number=data.get("TerminalNumber"),
            original_amount=data.get("OrginalAmount"),  # SEP's typo
            affective_amount=data.get("AffectiveAmount"),
            strace_date=data.get("StraceDate"),
            strace_no=data.get("StraceNo"),
        )


@dataclass
class VerifyResponse:
    """
    Response from SEP Verify/Reverse API.

    On success: Success=true, ResultCode=0
    On failure: Success=false, ResultCode=<error_code>

    ResultCode meanings (from SEP docs):
       0 = Success
      -2 = Transaction not found
      -6 = More than 30 minutes since transaction
       2 = Duplicate request
    -104 = Terminal inactive
    -105 = Terminal not found in system
    -106 = Unauthorized IP address
       5 = Transaction already reversed
    """

    success: bool
    result_code: int
    result_description: Optional[str] = None
    transaction_detail: Optional[VerifyTransactionDetail] = None
    raw_response: dict = field(default_factory=dict)

    @classmethod
    def from_sep_response(cls, data: dict) -> "VerifyResponse":
        """Parse SEP's Verify/Reverse JSON response."""
        detail_data = data.get("TransactionDetail")
        return cls(
            success=data.get("Success", False),
            result_code=data.get("ResultCode", -999),
            result_description=data.get("ResultDescription"),
            transaction_detail=VerifyTransactionDetail.from_sep_response(detail_data),
            raw_response=data,
        )

    @property
    def is_successful(self) -> bool:
        """Check if the verify/reverse was truly successful."""
        return self.success and self.result_code == 0

    @property
    def is_duplicate(self) -> bool:
        """Check if this is a duplicate verify/reverse request."""
        return self.result_code == 2

    @property
    def is_already_reversed(self) -> bool:
        """Check if the transaction was already reversed."""
        return self.result_code == 5

    @property
    def verified_amount(self) -> Optional[int]:
        """Get the verified original amount (what was charged)."""
        if self.transaction_detail:
            return self.transaction_detail.original_amount
        return None


@dataclass
class CallbackData:
    """
    Data received when SEP redirects buyer back to our callback URL.

    SEP sends this as POST form data to our RedirectURL.
    We parse it from the form/query parameters.

    From SEP docs (case-sensitive!):
    - MID: Terminal number
    - State: Transaction state (string: "OK", "CanceledByUser", etc.)
    - Status: Transaction status (numeric code)
    - RRN: Reference number
    - RefNum: Digital receipt (up to 50 chars, UNIQUE)
    - ResNum: Our order number (what we sent)
    - TerminalId: Terminal ID
    - TraceNo: Trace number
    - Amount: Transaction amount
    - Wage: Fee amount (for multi-settlement merchants)
    - SecurePan: Masked card number (e.g., "621986****8080")
    - HashedCardNumber: SHA256 hashed card number
    """

    mid: Optional[str] = None
    state: Optional[str] = None
    status: Optional[int] = None
    rrn: Optional[str] = None
    ref_num: Optional[str] = None
    res_num: Optional[str] = None
    terminal_id: Optional[str] = None
    trace_no: Optional[str] = None
    amount: Optional[int] = None
    wage: Optional[int] = None
    secure_pan: Optional[str] = None
    hashed_card_number: Optional[str] = None
    token: Optional[str] = None
    
    @classmethod
    def from_form_data(cls, data: dict) -> "CallbackData":
        """
        Parse callback data from SEP's POST form data.

        Uses direct string keys matching SEP's exact PascalCase names
        since SEPParams callback constants use CB_ prefix.
        """
        def safe_int(value) -> Optional[int]:
            if value is None:
                return None
            try:
                return int(value)
            except (ValueError, TypeError):
                return None

        return cls(
            mid=data.get("MID"),
            state=data.get("State"),
            status=safe_int(data.get("Status")),
            rrn=data.get("RRN") or data.get("Rrn"),
            ref_num=data.get("RefNum"),
            res_num=data.get("ResNum"),
            terminal_id=data.get("TerminalId"),
            trace_no=data.get("TraceNo"),
            amount=safe_int(data.get("Amount")),
            wage=safe_int(data.get("Wage")),
            secure_pan=data.get("SecurePan"),
            hashed_card_number=data.get("HashedCardNumber"),
            token=data.get("Token"),
        )

    @property
    def is_ok(self) -> bool:
        """Check if SEP reports the transaction as successful."""
        return self.state == "OK" and self.status == 2

    @property
    def has_ref_num(self) -> bool:
        """
        Check if a RefNum was provided.
        Per SEP docs: empty RefNum means a problem occurred during transaction.
        """
        return bool(self.ref_num and self.ref_num.strip())

    @property
    def status_description(self) -> str:
        """Get human-readable description of the callback status."""
        if self.status is not None:
            info = SEP_CALLBACK_STATUS_CODES.get(self.status, {})
            return info.get(
                "description_en",
                f"Unknown status code: {self.status}"
            )
        return "No status code received"


# ─────────────────────────────────────────────────────────────
# SEP Gateway Client
# ─────────────────────────────────────────────────────────────


class SEPClient:
    """
    HTTP client for Saman Electronic Payment (SEP) gateway.

    Handles all communication with SEP's REST APIs:
    1. Token acquisition (POST /OnlinePG/OnlinePG with action=token)
    2. Transaction verification (POST /verifyTxnRandomSessionkey/ipg/VerifyTransaction)
    3. Transaction reversal (POST /verifyTxnRandomSessionkey/ipg/ReverseTransaction)

    Uses httpx.AsyncClient for async HTTP requests with:
    - Connection pooling
    - Automatic retry for network errors (not for business logic errors)
    - Configurable timeouts
    - Full request/response logging
    - Prometheus metrics for latency and error tracking
    """

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """
        Get or create the httpx async client.

        Uses a persistent client for connection pooling.
        Timeout is set to 30 seconds — SEP can be slow.
        """
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=10.0,    # 10s to establish connection
                    read=30.0,       # 30s to read response (SEP can be slow)
                    write=10.0,      # 10s to send request
                    pool=10.0,       # 10s to get connection from pool
                ),
                # Do NOT follow redirects — we handle them explicitly
                follow_redirects=False,
                # Verify SSL — SEP uses valid certificates
                verify=True,
                # Connection pool limits
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                    keepalive_expiry=30,
                ),
            )
        return self._client

    async def close(self):
        """Close the HTTP client. Call on app shutdown."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
            logger.info("sep_client_closed")

    # ─────────────────────────────────────────────────────────
    # 1. TOKEN API
    # ─────────────────────────────────────────────────────────

    async def request_token(
        self,
        amount: int,
        res_num: str,
        redirect_url: str,
        cell_number: Optional[str] = None,
        wage: Optional[int] = None,
        token_expiry_min: Optional[int] = None,
    ) -> TokenResponse:
        """
        Request a payment token from SEP.

        This is Step 1 of the payment flow. The token is used to redirect
        the buyer to SEP's payment page.

        SEP Endpoint: POST {SEP_PAYMENT_URL}
        SEP Docs Section: "1. دریافت توکن"

        Args:
            amount: Payment amount in Rials (must be positive integer).
                    This is the amount AFTER discount has been applied.
            res_num: Our unique order/reservation number. SEP returns this
                     in the callback so we can match it to our payment record.
                     Must be unique per transaction to prevent duplicate payments.
            redirect_url: URL where SEP will redirect the buyer after payment.
                         SEP sends POST data to this URL with transaction results.
            cell_number: Optional. Buyer's mobile number (without leading 0).
                        If SEP has saved cards for this number, buyer sees them.
                        Format: "9120000000" (10 digits, no leading 0)
            wage: Optional. Fee amount for multi-settlement merchants.
                  Total charged to buyer = amount + wage.
                  We don't use this (per requirements).
            token_expiry_min: Optional. Token validity in minutes (20-3600).
                             Default is 20 minutes if not sent.

        Returns:
            TokenResponse with success status and token (or error details).

        Raises:
            SEPTokenException: If SEP returns an error response.
            SEPTimeoutException: If SEP doesn't respond within timeout.
            SEPConnectionException: If network error connecting to SEP.
        """
        start_time = track_time()
        terminal_id = payment_settings.SEP_TERMINAL_ID

        # Build request payload — EXACT parameter names from SEP docs
        # SEP docs: "سیستم نسبت به حروف بزرگ و کوچک حساس است"
        payload: dict[str, Any] = {
            SEPParams.ACTION: "token",
            SEPParams.TERMINAL_ID: terminal_id,
            SEPParams.AMOUNT: amount,
            SEPParams.RES_NUM: res_num,
            SEPParams.REDIRECT_URL: redirect_url,
        }

        # Optional parameters
        if cell_number:
            payload[SEPParams.CELL_NUMBER] = cell_number
        if wage is not None:
            payload[SEPParams.WAGE] = wage
        if token_expiry_min is not None:
            # SEP clamps this to 20-3600 range automatically,
            # but we do it ourselves for clarity in logs
            clamped = max(20, min(3600, token_expiry_min))
            payload["TokenExpiryInMin"] = clamped

        logger.info(
            "sep_token_request",
            terminal_id=terminal_id,
            amount=amount,
            res_num=res_num,
            redirect_url=redirect_url[:50] + "..." if len(redirect_url) > 50 else redirect_url,
        )

        try:
            client = await self._get_client()
            response = await client.post(
                payment_settings.SEP_PAYMENT_URL,
                json=payload,
            )

            duration = duration_since(start_time)
            response_data = response.json()

            logger.info(
                "sep_token_response",
                status_code=response.status_code,
                sep_status=response_data.get("status"),
                duration=round(duration, 3),
                has_token=bool(response_data.get("token")),
            )

            result = TokenResponse.from_sep_response(response_data)

            # Record metrics
            if result.success:
                metrics.payment_token_obtained.labels(
                    terminal_id=terminal_id
                ).inc()
                metrics.record_sep_api_call("token", duration, True)
            else:
                error_code = result.error_code or "unknown"
                metrics.payment_token_failed.labels(
                    terminal_id=terminal_id,
                    error_code=error_code,
                ).inc()
                metrics.record_sep_api_call(
                    "token", duration, False, error_type=f"sep_error_{error_code}"
                )

                logger.warning(
                    "sep_token_failed",
                    error_code=result.error_code,
                    error_desc=result.error_desc,
                    terminal_id=terminal_id,
                    res_num=res_num,
                )

            return result

        except httpx.TimeoutException as e:
            duration = duration_since(start_time)
            metrics.record_sep_api_call("token", duration, False, error_type="timeout")
            logger.error(
                "sep_token_timeout",
                error=str(e),
                terminal_id=terminal_id,
                res_num=res_num,
                duration=round(duration, 3),
            )
            raise SEPTimeoutException(
                f"SEP token request timed out after {round(duration, 1)}s"
            ) from e

        except httpx.ConnectError as e:
            duration = duration_since(start_time)
            metrics.record_sep_api_call("token", duration, False, error_type="connection")
            logger.error(
                "sep_token_connection_error",
                error=str(e),
                terminal_id=terminal_id,
                url=payment_settings.SEP_PAYMENT_URL,
            )
            raise SEPConnectionException(
                f"Failed to connect to SEP: {str(e)}"
            ) from e

        except httpx.HTTPError as e:
            duration = duration_since(start_time)
            metrics.record_sep_api_call("token", duration, False, error_type="http_error")
            logger.error(
                "sep_token_http_error",
                error=str(e),
                terminal_id=terminal_id,
            )
            raise SEPGatewayException(
                f"SEP token request failed: {str(e)}"
            ) from e

        except Exception as e:
            duration = duration_since(start_time)
            metrics.record_sep_api_call("token", duration, False, error_type="unexpected")
            logger.error(
                "sep_token_unexpected_error",
                error=str(e),
                error_type=type(e).__name__,
                terminal_id=terminal_id,
            )
            raise SEPGatewayException(
                f"Unexpected error during SEP token request: {str(e)}"
            ) from e

    # ─────────────────────────────────────────────────────────
    # 2. VERIFY API
    # ─────────────────────────────────────────────────────────

    async def verify_transaction(
        self,
        ref_num: str,
        max_retries: Optional[int] = None,
        retry_delay: Optional[float] = None,
    ) -> VerifyResponse:
        """
        Verify (confirm) a transaction with SEP.

        This is the critical step after receiving a successful callback.
        Must be called within 30 MINUTES of the transaction, otherwise
        SEP automatically reverses the transaction.

        SEP Endpoint: POST {SEP_VERIFY_URL}
        SEP Docs Section: "6. مکانیزم مطلع سازی پذیرنده از انجام موفق تراکنش (Verify)"

        IMPORTANT (from SEP docs):
        - TerminalNumber is sent as INTEGER (int64), not string!
          (This differs from Token API which uses TerminalId as string)
        - If verify response doesn't arrive (timeout/network issue),
          RETRY within 30 minutes. Only stop retrying if you get an
          actual error response (negative ResultCode).
        - ResultCode 0 = success. The verified amount is in
          TransactionDetail.OrginalAmount (SEP's typo, not ours).
        - ResultCode 2 = duplicate request (already verified — treat as success).

        Args:
            ref_num: The digital receipt number from SEP callback (RefNum).
            max_retries: Number of retry attempts for network failures.
                        Defaults to PAYMENT_VERIFY_MAX_RETRIES from config.
            retry_delay: Delay between retries in seconds.
                        Defaults to PAYMENT_VERIFY_RETRY_DELAY from config.

        Returns:
            VerifyResponse with verification result.

        Raises:
            SEPVerifyException: If all retries exhausted without getting a response.
            SEPTimeoutException: If SEP doesn't respond (after all retries).
        """
        retries = max_retries if max_retries is not None else payment_settings.PAYMENT_VERIFY_MAX_RETRIES
        delay = retry_delay if retry_delay is not None else payment_settings.PAYMENT_VERIFY_RETRY_DELAY
        terminal_number = int(payment_settings.SEP_TERMINAL_ID)

        # Build request payload
        # CRITICAL: TerminalNumber is INTEGER here, not string!
        # SEP docs: "TerminalNumber: Int64"
        payload = {
            SEPParams.VR_REF_NUM: ref_num,
            SEPParams.VR_TERMINAL_NUMBER: terminal_number,
        }

        start_time = track_time()
        last_error: Optional[Exception] = None

        for attempt in range(1, retries + 1):
            try:
                logger.info(
                    "sep_verify_request",
                    ref_num=ref_num[:20] + "..." if len(ref_num) > 20 else ref_num,
                    terminal_number=terminal_number,
                    attempt=attempt,
                    max_retries=retries,
                )

                client = await self._get_client()
                response = await client.post(
                    payment_settings.SEP_VERIFY_URL,
                    json=payload,
                )

                duration = duration_since(start_time)
                response_data = response.json()

                result = VerifyResponse.from_sep_response(response_data)

                logger.info(
                    "sep_verify_response",
                    ref_num=ref_num[:20] + "...",
                    result_code=result.result_code,
                    success=result.success,
                    verified_amount=result.verified_amount,
                    duration=round(duration, 3),
                    attempt=attempt,
                )

                # Record metrics
                if result.is_successful or result.is_duplicate:
                    metrics.payment_verified.labels(
                        terminal_id=payment_settings.SEP_TERMINAL_ID
                    ).inc()
                    metrics.record_sep_api_call("verify", duration, True)
                else:
                    metrics.payment_verify_failed.labels(
                        terminal_id=payment_settings.SEP_TERMINAL_ID,
                        result_code=str(result.result_code),
                    ).inc()
                    metrics.record_sep_api_call(
                        "verify", duration, False,
                        error_type=f"result_code_{result.result_code}"
                    )

                # Got a response (even if error) — don't retry
                # SEP docs: "تکرار در صورتی باید انجام شود که جواب به
                # دست فروشنده نرسد، نه اینکه نتیجه VerifyTransaction
                # نشان دهنده خطا باشد (منفی باشد)"
                return result

            except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPError) as e:
                last_error = e
                duration = duration_since(start_time)

                logger.warning(
                    "sep_verify_retry",
                    ref_num=ref_num[:20] + "...",
                    attempt=attempt,
                    max_retries=retries,
                    error=str(e),
                    error_type=type(e).__name__,
                    duration=round(duration, 3),
                )

                if attempt < retries:
                    # Wait before retry — SEP docs say keep trying within 30 min
                    import asyncio
                    await asyncio.sleep(delay * attempt)  # Linear backoff
                else:
                    metrics.record_sep_api_call(
                        "verify", duration, False,
                        error_type="all_retries_exhausted"
                    )

        # All retries exhausted
        total_duration = duration_since(start_time)
        error_msg = (
            f"SEP verify failed after {retries} attempts "
            f"({round(total_duration, 1)}s total). "
            f"Last error: {str(last_error)}"
        )

        logger.error(
            "sep_verify_all_retries_failed",
            ref_num=ref_num[:20] + "...",
            retries=retries,
            total_duration=round(total_duration, 3),
            last_error=str(last_error),
        )

        if isinstance(last_error, httpx.TimeoutException):
            raise SEPTimeoutException(error_msg) from last_error
        else:
            raise SEPVerifyException(error_msg) from last_error

    # ─────────────────────────────────────────────────────────
    # 3. REVERSE API
    # ─────────────────────────────────────────────────────────

    async def reverse_transaction(
        self,
        ref_num: str,
    ) -> VerifyResponse:
        """
        Reverse (void) a verified transaction with SEP.

        Can only be called within 50 MINUTES of the transaction time.
        The full amount is returned to the cardholder.

        SEP Endpoint: POST {SEP_REVERSE_URL}
        SEP Docs Section: "10. اصلاحیه تراکنش یا Reverse"

        IMPORTANT:
        - Same request/response format as Verify
        - TerminalNumber is INTEGER (int64)
        - Only works on transactions that have been verified (status=VERIFIED)
        - ResultCode 0 = successfully reversed
        - ResultCode 2 = duplicate reverse request (already reversed)

        Args:
            ref_num: The digital receipt number (RefNum) of the transaction to reverse.

        Returns:
            VerifyResponse with reversal result.

        Raises:
            SEPReverseException: If the reverse request fails.
            SEPTimeoutException: If SEP doesn't respond.
        """
        start_time = track_time()
        terminal_number = int(payment_settings.SEP_TERMINAL_ID)

        # Same parameter format as Verify
        payload = {
            SEPParams.VR_REF_NUM: ref_num,
            SEPParams.VR_TERMINAL_NUMBER: terminal_number,
        }

        logger.info(
            "sep_reverse_request",
            ref_num=ref_num[:20] + "..." if len(ref_num) > 20 else ref_num,
            terminal_number=terminal_number,
        )

        try:
            client = await self._get_client()
            response = await client.post(
                payment_settings.SEP_REVERSE_URL,
                json=payload,
            )

            duration = duration_since(start_time)
            response_data = response.json()

            result = VerifyResponse.from_sep_response(response_data)

            logger.info(
                "sep_reverse_response",
                ref_num=ref_num[:20] + "...",
                result_code=result.result_code,
                success=result.success,
                duration=round(duration, 3),
            )

            # Record metrics
            if result.is_successful or result.is_duplicate:
                metrics.reverse_completed.labels(
                    terminal_id=payment_settings.SEP_TERMINAL_ID
                ).inc()
                metrics.record_sep_api_call("reverse", duration, True)
            else:
                metrics.reverse_failed.labels(
                    terminal_id=payment_settings.SEP_TERMINAL_ID,
                    result_code=str(result.result_code),
                ).inc()
                metrics.record_sep_api_call(
                    "reverse", duration, False,
                    error_type=f"result_code_{result.result_code}"
                )

            return result

        except httpx.TimeoutException as e:
            duration = duration_since(start_time)
            metrics.record_sep_api_call("reverse", duration, False, error_type="timeout")
            logger.error(
                "sep_reverse_timeout",
                ref_num=ref_num[:20] + "...",
                error=str(e),
                duration=round(duration, 3),
            )
            raise SEPTimeoutException(
                f"SEP reverse request timed out after {round(duration, 1)}s"
            ) from e

        except httpx.ConnectError as e:
            duration = duration_since(start_time)
            metrics.record_sep_api_call("reverse", duration, False, error_type="connection")
            logger.error(
                "sep_reverse_connection_error",
                ref_num=ref_num[:20] + "...",
                error=str(e),
            )
            raise SEPConnectionException(
                f"Failed to connect to SEP for reverse: {str(e)}"
            ) from e

        except httpx.HTTPError as e:
            duration = duration_since(start_time)
            metrics.record_sep_api_call("reverse", duration, False, error_type="http_error")
            logger.error(
                "sep_reverse_http_error",
                ref_num=ref_num[:20] + "...",
                error=str(e),
            )
            raise SEPReverseException(
                f"SEP reverse request failed: {str(e)}"
            ) from e

    # ─────────────────────────────────────────────────────────
    # HELPER METHODS
    # ─────────────────────────────────────────────────────────

    def build_redirect_url(self, token: str) -> str:
        """
        Build the URL to redirect the buyer to SEP's payment page.

        Two methods exist (per SEP docs):
        1. POST form with hidden Token field → auto-submit
        2. GET redirect to SendToken URL ← We use this one

        We use method 2 (GET redirect) because:
        - Simpler — just a URL, no HTML form needed
        - Frontend can do window.location.href = url
        - Works with any frontend framework

        Note from SEP docs: When using GET redirect, the GetMethod
        parameter cannot be sent (always POST callback).

        Args:
            token: The token received from request_token().

        Returns:
            Full URL to redirect the buyer to.
        """
        base_url = payment_settings.SEP_PAYMENT_URL.replace(
            "/OnlinePG/OnlinePG", "/OnlinePG/SendToken"
        )
        return f"{base_url}?token={token}"

    @staticmethod
    def get_result_code_description(result_code: int) -> str:
        """
        Get human-readable description for a SEP result code.

        Args:
            result_code: The ResultCode from Verify/Reverse response.

        Returns:
            Description string (English).
        """
        info = SEP_RESULT_CODES.get(result_code)
        if info:
            return f"{info.get('description_en', '')} ({info.get('description_fa', '')})"
        return f"Unknown result code: {result_code}"

    @staticmethod
    def get_callback_status_description(status_code: int) -> str:
        """
        Get human-readable description for a SEP callback status code.

        Args:
            status_code: The Status code from callback data.

        Returns:
            Description string (English).
        """
        info = SEP_CALLBACK_STATUS_CODES.get(status_code)
        if info:
            return info.get("description_en", f"Unknown: {status_code}")
        return f"Unknown status code: {status_code}"


# Singleton instance — import this in services
sep_client = SEPClient()
