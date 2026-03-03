"""
Prometheus Metrics for Payment Service.

Provides counters, histograms, and gauges to monitor:
- Payment initiation, callback, and verification
- Reverse transactions
- Wallet operations (credit/debit)
- Discount code usage
- SEP API call latency and error rates
- Double-spending prevention events
- Lock acquisition metrics

All metrics are prefixed with 'payment_' to avoid collisions with
other services' metrics.

Usage:
    from app.payment.core.metrics import metrics

    # Record a successful payment
    metrics.payment_initiated.labels(terminal_id="12345").inc()

    # Time an SEP API call
    with metrics.sep_api_latency.labels(endpoint="verify").time():
        result = await sep_client.verify(...)

    # In your FastAPI app, expose metrics:
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    
    @app.get("/metrics")
    async def prometheus_metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
"""

import time
import functools
from typing import Callable, Any

import structlog
from prometheus_client import Counter, Histogram, Gauge, Info

logger = structlog.get_logger()


class PaymentMetrics:
    """
    Centralized Prometheus metrics for the payment service.
    
    All metric names are prefixed with 'payment_' and follow
    Prometheus naming conventions.
    """

    def __init__(self):
        # ─────────────────────────────────────────────────────
        # Payment Flow Metrics
        # ─────────────────────────────────────────────────────
        
        self.payment_initiated = Counter(
            "payment_initiated_total",
            "Total number of payment initiations (token requests to SEP)",
            ["terminal_id"],
        )

        self.payment_token_obtained = Counter(
            "payment_token_obtained_total",
            "Successful token acquisitions from SEP",
            ["terminal_id"],
        )

        self.payment_token_failed = Counter(
            "payment_token_failed_total",
            "Failed token acquisitions from SEP",
            ["terminal_id", "error_code"],
        )

        self.payment_callback_received = Counter(
            "payment_callback_received_total",
            "Total callbacks received from SEP",
            ["state", "status_code"],
        )

        self.payment_verified = Counter(
            "payment_verified_total",
            "Successfully verified payments",
            ["terminal_id"],
        )

        self.payment_verify_failed = Counter(
            "payment_verify_failed_total",
            "Failed payment verifications",
            ["terminal_id", "result_code"],
        )

        self.payment_completed = Counter(
            "payment_completed_total",
            "Fully completed payments (verified + wallet credited)",
            ["terminal_id"],
        )

        self.payment_amount_total = Counter(
            "payment_amount_rials_total",
            "Total amount of successful payments in Rials",
            ["terminal_id"],
        )

        # ─────────────────────────────────────────────────────
        # Reverse Metrics
        # ─────────────────────────────────────────────────────

        self.reverse_requested = Counter(
            "payment_reverse_requested_total",
            "Total reverse transaction requests",
            ["terminal_id"],
        )

        self.reverse_completed = Counter(
            "payment_reverse_completed_total",
            "Successfully completed reverses",
            ["terminal_id"],
        )

        self.reverse_failed = Counter(
            "payment_reverse_failed_total",
            "Failed reverse attempts",
            ["terminal_id", "result_code"],
        )

        # ─────────────────────────────────────────────────────
        # Wallet Metrics
        # ─────────────────────────────────────────────────────

        self.wallet_credited = Counter(
            "payment_wallet_credited_total",
            "Total wallet credit operations",
            [],
        )

        self.wallet_credit_amount = Counter(
            "payment_wallet_credit_amount_rials_total",
            "Total amount credited to wallets in Rials",
            [],
        )

        self.wallet_debit_amount = Counter(
            "payment_wallet_debit_amount_rials_total",
            "Total amount debited from wallets in Rials",
            [],
        )

        # ─────────────────────────────────────────────────────
        # Discount Metrics
        # ─────────────────────────────────────────────────────

        self.discount_applied = Counter(
            "payment_discount_applied_total",
            "Number of discount codes successfully applied",
            ["discount_type"],
        )

        self.discount_amount_total = Counter(
            "payment_discount_amount_rials_total",
            "Total discount amount given in Rials",
            ["discount_type"],
        )

        self.discount_validation_failed = Counter(
            "payment_discount_validation_failed_total",
            "Failed discount code validations",
            ["reason"],
        )

        # ─────────────────────────────────────────────────────
        # SEP API Latency
        # ─────────────────────────────────────────────────────

        self.sep_api_latency = Histogram(
            "payment_sep_api_latency_seconds",
            "Latency of SEP API calls in seconds",
            ["endpoint"],  # token, verify, reverse
            buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
        )

        self.sep_api_errors = Counter(
            "payment_sep_api_errors_total",
            "SEP API call errors (network, timeout, etc.)",
            ["endpoint", "error_type"],
        )

        # ─────────────────────────────────────────────────────
        # Payment Processing Duration
        # ─────────────────────────────────────────────────────

        self.payment_processing_duration = Histogram(
            "payment_processing_duration_seconds",
            "End-to-end payment processing duration (callback to completion)",
            ["status"],  # success, failed
            buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
        )

        self.reverse_processing_duration = Histogram(
            "payment_reverse_processing_duration_seconds",
            "Reverse transaction processing duration",
            ["status"],
            buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
        )

        # ─────────────────────────────────────────────────────
        # Security Metrics
        # ─────────────────────────────────────────────────────

        self.double_spend_prevented = Counter(
            "payment_double_spend_prevented_total",
            "Number of double-spending attempts prevented",
            ["layer"],  # database, redis_lock, application_check
        )

        self.lock_acquired = Counter(
            "payment_lock_acquired_total",
            "Distributed lock acquisitions",
            ["resource_type"],  # payment, reverse, wallet, callback
        )

        self.lock_failed = Counter(
            "payment_lock_failed_total",
            "Failed lock acquisitions",
            ["resource_type"],
        )

        self.lock_wait_duration = Histogram(
            "payment_lock_wait_duration_seconds",
            "Time spent waiting for lock acquisition",
            ["resource_type"],
            buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
        )

        # ─────────────────────────────────────────────────────
        # Active Operations Gauge
        # ─────────────────────────────────────────────────────

        self.active_payments = Gauge(
            "payment_active_processing",
            "Number of payments currently being processed",
        )

        self.active_reverses = Gauge(
            "payment_active_reverses",
            "Number of reverse transactions currently being processed",
        )

        # ─────────────────────────────────────────────────────
        # Service Info
        # ─────────────────────────────────────────────────────

        self.service_info = Info(
            "payment_service",
            "Payment service metadata",
        )
        self.service_info.info({
            "version": "1.0.0",
            "psp": "saman_electronic_payment",
            "psp_name": "sep",
        })

    # ─────────────────────────────────────────────────────────
    # Convenience Methods
    # ─────────────────────────────────────────────────────────

    def record_payment_success(
        self,
        terminal_id: str,
        amount: int,
        processing_time: float,
    ):
        """Record a fully successful payment (verified + wallet credited)."""
        self.payment_completed.labels(terminal_id=terminal_id).inc()
        self.payment_amount_total.labels(terminal_id=terminal_id).inc(amount)
        self.payment_processing_duration.labels(status="success").observe(
            processing_time
        )
        logger.info(
            "metric_payment_success",
            terminal_id=terminal_id,
            amount=amount,
            processing_time=round(processing_time, 3),
        )

    def record_payment_failure(
        self,
        terminal_id: str,
        reason: str,
        processing_time: float,
    ):
        """Record a failed payment."""
        self.payment_verify_failed.labels(
            terminal_id=terminal_id, result_code=reason
        ).inc()
        self.payment_processing_duration.labels(status="failed").observe(
            processing_time
        )
        logger.info(
            "metric_payment_failure",
            terminal_id=terminal_id,
            reason=reason,
            processing_time=round(processing_time, 3),
        )

    def record_reverse_success(
        self,
        terminal_id: str,
        amount: int,
        processing_time: float,
    ):
        """Record a successful reverse."""
        self.reverse_completed.labels(terminal_id=terminal_id).inc()
        self.reverse_processing_duration.labels(status="success").observe(
            processing_time
        )
        logger.info(
            "metric_reverse_success",
            terminal_id=terminal_id,
            amount=amount,
            processing_time=round(processing_time, 3),
        )

    def record_reverse_failure(
        self,
        terminal_id: str,
        reason: str,
        processing_time: float,
    ):
        """Record a failed reverse."""
        self.reverse_failed.labels(
            terminal_id=terminal_id, result_code=reason
        ).inc()
        self.reverse_processing_duration.labels(status="failed").observe(
            processing_time
        )
        logger.info(
            "metric_reverse_failure",
            terminal_id=terminal_id,
            reason=reason,
            processing_time=round(processing_time, 3),
        )

    def record_discount_applied(
        self,
        discount_type: str,
        discount_amount: int,
    ):
        """Record a successfully applied discount."""
        self.discount_applied.labels(discount_type=discount_type).inc()
        self.discount_amount_total.labels(discount_type=discount_type).inc(
            discount_amount
        )

    def record_double_spend_attempt(self, layer: str):
        """Record a prevented double-spending attempt."""
        self.double_spend_prevented.labels(layer=layer).inc()
        logger.warning("double_spend_prevented", layer=layer)

    def record_sep_api_call(
        self,
        endpoint: str,
        duration: float,
        success: bool,
        error_type: str = "",
    ):
        """Record an SEP API call with timing."""
        self.sep_api_latency.labels(endpoint=endpoint).observe(duration)
        if not success:
            self.sep_api_errors.labels(
                endpoint=endpoint, error_type=error_type
            ).inc()


def track_time() -> float:
    """Return current time for duration tracking. Use with time.monotonic()."""
    return time.monotonic()


def duration_since(start: float) -> float:
    """Calculate duration since start time."""
    return time.monotonic() - start


def timed_metric(metric_histogram, **labels):
    """
    Decorator to automatically time a function and record to a histogram.
    
    Usage:
        @timed_metric(metrics.sep_api_latency, endpoint="verify")
        async def verify_transaction(...):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.monotonic()
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                duration = time.monotonic() - start
                metric_histogram.labels(**labels).observe(duration)
        return wrapper
    return decorator


# Singleton instance — import this in services
metrics = PaymentMetrics()
