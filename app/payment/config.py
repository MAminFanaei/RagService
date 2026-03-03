"""
Payment Service Configuration

Reads SEP-specific settings from environment variables (.env file).
Uses pydantic-settings to provide type-safe, validated configuration.

This is separate from the main app's config.py to maintain modularity.
Both config classes read from the same .env file but each only cares
about its own variables (extra="ignore" ensures no conflicts).
"""

from pydantic_settings import BaseSettings
from functools import lru_cache


class PaymentSettings(BaseSettings):
    """
    Payment service configuration.
    
    All settings have sensible defaults for development.
    Override via .env file or environment variables in production.
    """

    # ─────────────────────────────────────────────────────────
    # SEP Gateway URLs
    # ─────────────────────────────────────────────────────────
    SEP_TERMINAL_ID: str = "0000"
    SEP_PAYMENT_URL: str = "https://sep.shaparak.ir/OnlinePG/OnlinePG"
    SEP_SEND_TOKEN_URL: str = "https://sep.shaparak.ir/OnlinePG/SendToken"
    SEP_VERIFY_URL: str = (
        "https://sep.shaparak.ir/verifyTxnRandomSessionkey/ipg/VerifyTransaction"
    )
    SEP_REVERSE_URL: str = (
        "https://sep.shaparak.ir/verifyTxnRandomSessionkey/ipg/ReverseTransaction"
    )

    # Token expiry: min 20, max 3600 minutes (SEP enforced)
    SEP_TOKEN_EXPIRY_MIN: int = 20

    # ─────────────────────────────────────────────────────────
    # Callback & Frontend URLs
    # ─────────────────────────────────────────────────────────
    
    # URL that SEP will POST callback data to after payment
    # Must match the RedirectURL sent during token request
    PAYMENT_CALLBACK_URL: str = "http://localhost:8000/api/v1/payment/callback"
    
    # Frontend URL where user is redirected after callback processing
    # Result params are appended as query string: ?payment_id=xxx&status=success
    FRONTEND_PAYMENT_RESULT_URL: str = "http://localhost:3000/payment/result"

    # ─────────────────────────────────────────────────────────
    # Payment Amount Limits (in Rials)
    # ─────────────────────────────────────────────────────────
    MIN_PAYMENT_AMOUNT: int = 10000
    MAX_PAYMENT_AMOUNT: int = 500000000
    
    # ─────────────────────────────────────────────────────────
    # Redis Distributed Lock Settings
    # ─────────────────────────────────────────────────────────
    
    # Max seconds to wait when trying to acquire a lock
    PAYMENT_LOCK_TIMEOUT: int = 30
    
    # Seconds before a lock auto-expires (safety net if process crashes)
    PAYMENT_LOCK_TTL: int = 300

    # ─────────────────────────────────────────────────────────
    # SEP Verify Retry Settings
    # ─────────────────────────────────────────────────────────
    
    # Per SEP docs: if verify response doesn't arrive, retry.
    # Only retry on timeout/network error, NOT on error responses.
    PAYMENT_VERIFY_MAX_RETRIES: int = 3
    PAYMENT_VERIFY_RETRY_DELAY: int = 2  # seconds between retries

    # ─────────────────────────────────────────────────────────
    # Business Rules
    # ─────────────────────────────────────────────────────────
    
    # SEP allows reverse within 50 minutes of transaction
    PAYMENT_REVERSE_WINDOW_MINUTES: int = 50
    
    # HTTP client timeout for SEP API calls (seconds)
    SEP_HTTP_TIMEOUT: int = 30

    model_config = {
        "extra": "ignore",  # Ignore vars from main app (DB_*, REDIS_*, etc.)
        "case_sensitive": True,
    }


@lru_cache()
def get_payment_settings() -> PaymentSettings:
    """
    Cached payment settings singleton.
    
    Uses lru_cache to ensure settings are only loaded once from .env.
    The same instance is reused throughout the application lifecycle.
    """
    return PaymentSettings()


# Module-level convenience reference
# Usage: from app.payment.config import payment_settings
payment_settings = get_payment_settings()
