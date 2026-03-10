"""
Payment routers package.

Combines all payment-related routers into a single router.
"""

from fastapi import APIRouter

from app.payment.routers.health import router as health_router
from app.payment.routers.initiate import router as initiate_router
from app.payment.routers.callback import router as callback_router
from app.payment.routers.reverse import router as reverse_router
from app.payment.routers.query import router as query_router
from app.payment.routers.wallet import router as wallet_router
from app.payment.routers.discount import router as discount_router
from app.payment.routers.metrics import router as metrics_router
# Main payment router — all sub-routers are included here
payment_router = APIRouter()

# Health (no auth required)
payment_router.include_router(health_router)
payment_router.include_router(metrics_router)
# Payment operations
payment_router.include_router(initiate_router)
payment_router.include_router(callback_router)
payment_router.include_router(reverse_router)
payment_router.include_router(query_router)

# Wallet
payment_router.include_router(wallet_router, prefix="/wallet")

# Discount
payment_router.include_router(discount_router, prefix="/discount")

__all__ = ["payment_router"]
