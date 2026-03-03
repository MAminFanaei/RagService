"""
Payment Models Package

All SQLAlchemy models for the payment service.
Importing this package registers all models with Base,
which is required for Alembic migrations to detect them.

Tables created:
    - wallets             → One wallet per user, stores balance
    - wallet_transactions → Ledger of all wallet credits/debits
    - payments            → Payment records (SEP transactions)
    - reverses            → Reverse/refund records
    - discount_codes      → Discount code definitions
    - discount_usages     → Tracks which user used which code

All models use the same Base from app.core.database,
so they coexist with existing models (users, chats, etc.)
in the same database and migration chain.
"""

# Import order: tables with no FKs first, then dependent tables
from app.payment.models.wallet import Wallet, WalletTransaction
from app.payment.models.discount import DiscountCode, DiscountUsage
from app.payment.models.payment import Payment
from app.payment.models.reverse import Reverse

__all__ = [
    "Wallet",
    "WalletTransaction",
    "Payment",
    "Reverse",
    "DiscountCode",
    "DiscountUsage",
]
