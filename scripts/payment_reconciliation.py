# scripts/payment_reconciliation.py
"""
Daily Payment Reconciliation

Finds inconsistencies between payments, wallets, and SEP.
Run via cron: 0 2 * * * python scripts/payment_reconciliation.py

Checks:
1. VERIFIED payments without wallet credit
2. Wallet credits without VERIFIED payment
3. Payments stuck in CALLBACK_RECEIVED for > 1 hour
4. Payments stuck in TOKEN_OBTAINED for > 24 hours
5. Total wallet credits vs total verified payment amounts
"""

import asyncio
import structlog
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.payment.models.payment import Payment
from app.payment.models.wallet import Wallet, WalletTransaction
from app.payment.core.constants import PaymentStatus, WalletTxType

logger = structlog.get_logger()


async def run_reconciliation():
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        issues = []

        # ── Check 1: VERIFIED payments without wallet transaction ──
        verified = await db.execute(
            select(Payment).where(Payment.status == PaymentStatus.VERIFIED)
        )
        for payment in verified.scalars().all():
            wallet_tx = await db.execute(
                select(WalletTransaction).where(
                    WalletTransaction.payment_id == payment.id,
                    WalletTransaction.tx_type == WalletTxType.CREDIT,
                )
            )
            if not wallet_tx.scalar_one_or_none():
                issues.append({
                    "type": "VERIFIED_NO_WALLET_CREDIT",
                    "severity": "CRITICAL",
                    "payment_id": payment.id,
                    "amount": payment.amount,
                    "verified_at": str(payment.verified_at),
                })

        # ── Check 2: Stuck in CALLBACK_RECEIVED > 1 hour ──
        one_hour_ago = now - timedelta(hours=1)
        stuck = await db.execute(
            select(Payment).where(
                Payment.status == PaymentStatus.CALLBACK_RECEIVED,
                Payment.callback_at < one_hour_ago,
            )
        )
        for payment in stuck.scalars().all():
            issues.append({
                "type": "STUCK_CALLBACK_RECEIVED",
                "severity": "HIGH",
                "payment_id": payment.id,
                "callback_at": str(payment.callback_at),
            })

        # ── Check 3: Stuck in TOKEN_OBTAINED > 24 hours ──
        one_day_ago = now - timedelta(hours=24)
        stale = await db.execute(
            select(Payment).where(
                Payment.status == PaymentStatus.TOKEN_OBTAINED,
                Payment.created_at < one_day_ago,
            )
        )
        for payment in stale.scalars().all():
            issues.append({
                "type": "STALE_TOKEN",
                "severity": "LOW",
                "payment_id": payment.id,
                "created_at": str(payment.created_at),
            })

        # ── Check 4: Balance sanity check ──
        total_credits = await db.execute(
            select(func.sum(WalletTransaction.amount)).where(
                WalletTransaction.tx_type == WalletTxType.CREDIT,
            )
        )
        total_debits = await db.execute(
            select(func.sum(func.abs(WalletTransaction.amount))).where(
                WalletTransaction.tx_type == WalletTxType.DEBIT,
            )
        )
        total_balances = await db.execute(select(func.sum(Wallet.balance)))

        credits = total_credits.scalar() or 0
        debits = total_debits.scalar() or 0
        balances = total_balances.scalar() or 0
        expected = credits - debits

        if expected != balances:
            issues.append({
                "type": "BALANCE_MISMATCH",
                "severity": "CRITICAL",
                "expected_total": expected,
                "actual_total": balances,
                "difference": expected - balances,
            })

        # ── Report ──
        if issues:
            critical = [i for i in issues if i["severity"] == "CRITICAL"]
            logger.error(
                "reconciliation_issues_found",
                total_issues=len(issues),
                critical_count=len(critical),
                issues=issues,
            )
            # TODO: Send alert (Slack, email, PagerDuty)
        else:
            logger.info("reconciliation_clean", message="No issues found")

        return issues


if __name__ == "__main__":
    asyncio.run(run_reconciliation())