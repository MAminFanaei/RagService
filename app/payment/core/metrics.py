"""
Prometheus Metrics for Payment Service.

Central metrics singleton used by all payment services.
"""

import time
import structlog
from prometheus_client import Counter, Histogram, Gauge, REGISTRY

logger = structlog.get_logger()


def _safe_metric(metric_cls, name, description, labelnames=None, **kwargs):
    """Create a Prometheus metric, or return existing one if already registered."""
    try:
        if labelnames:
            return metric_cls(name, description, labelnames, **kwargs)
        return metric_cls(name, description, **kwargs)
    except ValueError:
        return REGISTRY._names_to_collectors[name]


class PaymentMetrics:
    """Payment service Prometheus metrics — singleton."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True

        # --- Counters ---
        self._payment_initiated = _safe_metric(
            Counter, "payment_initiated_total", "Total payments initiated",
        )
        self._payment_token_obtained = _safe_metric(
            Counter, "payment_token_obtained_total",
            "Total payment tokens obtained",
            labelnames=["terminal_id"],
        )
        self._payment_token_failed = _safe_metric(
            Counter, "payment_token_failed_total",
            "Total payment token failures",
            labelnames=["terminal_id", "error_code"],
        )
        self._payment_verified = _safe_metric(
            Counter, "payment_verified_total",
            "Total payments successfully verified",
            labelnames=["terminal_id"],
        )
        self._payment_verify_failed = _safe_metric(
            Counter, "payment_verify_failed_total",
            "Total payment verify failures",
            labelnames=["terminal_id", "result_code"],
        )
        self._payment_failed = _safe_metric(
            Counter, "payment_failed_total", "Total payments failed",
            labelnames=["reason"],
        )
        self._payment_reversed_counter = _safe_metric(
            Counter, "payment_reversed_total", "Total payments reversed",
        )
        self._double_spend_blocked = _safe_metric(
            Counter, "payment_double_spend_blocked_total",
            "Total double-spend attempts blocked",
        )
        self._reverse_completed = _safe_metric(
            Counter, "payment_reverse_completed_total",
            "Total reversals completed",
            labelnames=["terminal_id"],
        )
        self._reverse_failed = _safe_metric(
            Counter, "payment_reverse_failed_total",
            "Total reversals failed",
            labelnames=["terminal_id", "result_code"],
        )
        self._wallet_credited = _safe_metric(
            Counter, "wallet_credited_total", "Total wallet credits",
        )
        self._wallet_debited = _safe_metric(
            Counter, "wallet_debited_total", "Total wallet debits",
        )
        self._discount_used = _safe_metric(
            Counter, "discount_used_total", "Total discount codes used",
        )
        self._sep_api_calls = _safe_metric(
            Counter, "sep_api_calls_total", "Total SEP API calls",
            labelnames=["endpoint", "success", "error_type"],
        )

        # --- Histograms ---
        self._payment_duration = _safe_metric(
            Histogram, "payment_duration_seconds",
            "Payment processing duration",
            buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
        )
        self._sep_api_duration = _safe_metric(
            Histogram, "sep_api_duration_seconds",
            "SEP API call duration",
            labelnames=["endpoint"],
            buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
        )
        self._payment_amount = _safe_metric(
            Histogram, "payment_amount_rials", "Payment amounts in Rials",
            buckets=[10000, 50000, 100000, 500000, 1000000, 5000000, 10000000, 50000000],
        )

        # --- Gauges ---
        self._active_payments = _safe_metric(
            Gauge, "payment_active_processing",
            "Number of payments currently being processed",
        )

    # ── Properties for direct access (used by sep_client.py) ──

    @property
    def payment_token_obtained(self):
        return self._payment_token_obtained

    @property
    def payment_token_failed(self):
        return self._payment_token_failed

    @property
    def payment_verified(self):
        return self._payment_verified

    @property
    def payment_verify_failed(self):
        return self._payment_verify_failed

    @property
    def reverse_completed(self):
        return self._reverse_completed

    @property
    def reverse_failed(self):
        return self._reverse_failed

    # ── Convenience Methods ──

    def payment_initiated(self):
        self._payment_initiated.inc()

    def payment_failed(self, reason: str = "unknown"):
        self._payment_failed.labels(reason=reason).inc()

    def payment_reversed(self, amount: int = 0):
        self._payment_reversed_counter.inc()
        if amount:
            self._payment_amount.observe(amount)

    def double_spend_blocked(self):
        self._double_spend_blocked.inc()

    def reverse_failed_metric(self):
        self._reverse_failed.labels(terminal_id="unknown", result_code="unknown").inc()

    def wallet_credited(self, amount: int = 0):
        self._wallet_credited.inc()
        if amount:
            self._payment_amount.observe(amount)

    def wallet_debited(self, amount: int = 0):
        self._wallet_debited.inc()

    def discount_used(self):
        self._discount_used.inc()

    def record_sep_api_call(self, endpoint: str, duration: float = 0,
                            success: bool = True, error_type: str = "none"):
        self._sep_api_calls.labels(
            endpoint=endpoint,
            success=str(success),
            error_type=error_type,
        ).inc()
        if duration:
            self._sep_api_duration.labels(endpoint=endpoint).observe(duration)

    def payment_processing_start(self):
        self._active_payments.inc()

    def payment_processing_end(self):
        self._active_payments.dec()


def track_time():
    """Return current time for duration tracking."""
    return time.time()


def duration_since(start_time: float) -> float:
    """Calculate duration since start_time."""
    return time.time() - start_time


# Singleton
metrics = PaymentMetrics()