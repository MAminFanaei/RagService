#!/usr/bin/env python3
"""
Manual cleanup script for soft-deleted users.

Permanently deletes chat sessions, messages, and message credits
for users deleted more than 30 days ago.

Payment and wallet records are NEVER deleted (legal compliance).

Usage:
    # Dry run (preview only)
    python scripts/cleanup_deleted_users.py --dry-run
    
    # Execute deletion
    python scripts/cleanup_deleted_users.py
    
    # Custom retention period (45 days)
    python scripts/cleanup_deleted_users.py --days 45
"""

import asyncio
import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from app.core.database import AsyncSessionLocal
from app.models.user import User
from app.models.chat import ChatSession
from app.models.message import Message
from app.models.credit import MessageCredit
import structlog

logger = structlog.get_logger()


async def get_users_for_cleanup(
    db: AsyncSession,
    retention_days: int = 30
) -> list[User]:
    """
    Get users deleted more than {retention_days} days ago.
    """
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)
    
    result = await db.execute(
        select(User).where(
            User.is_active == False,
            User.deleted_at.isnot(None),
            User.deleted_at < cutoff_date
        )
    )
    return result.scalars().all()


async def cleanup_user_data(
    db: AsyncSession,
    user_id: str,
    dry_run: bool = False
) -> dict:
    """
    Delete chat sessions, messages, and credits for a user.
    Returns dict with deletion counts.
    """
    stats = {
        "user_id": user_id,
        "chats_deleted": 0,
        "messages_deleted": 0,
        "credits_deleted": 0
    }
    
    # Count messages
    result = await db.execute(
        select(func.count())
        .select_from(Message)
        .join(ChatSession)
        .where(ChatSession.user_id == user_id)
    )
    stats["messages_deleted"] = result.scalar() or 0
    
    # Count chats
    result = await db.execute(
        select(func.count())
        .select_from(ChatSession)
        .where(ChatSession.user_id == user_id)
    )
    stats["chats_deleted"] = result.scalar() or 0
    
    # Count credits
    result = await db.execute(
        select(func.count())
        .select_from(MessageCredit)
        .where(MessageCredit.user_id == user_id)
    )
    stats["credits_deleted"] = result.scalar() or 0
    
    if not dry_run:
        # Delete chats (messages cascade via relationship)
        await db.execute(
            delete(ChatSession).where(ChatSession.user_id == user_id)
        )
        
        # Delete credits
        await db.execute(
            delete(MessageCredit).where(MessageCredit.user_id == user_id)
        )
        
        await db.commit()
    
    return stats


async def main(dry_run: bool = False, retention_days: int = 30):
    """
    Main cleanup routine.
    """
    logger.info(
        "cleanup_started",
        dry_run=dry_run,
        retention_days=retention_days
    )
    
    async with AsyncSessionLocal() as db:
        users = await get_users_for_cleanup(db, retention_days)
        
        if not users:
            logger.info("no_users_to_cleanup")
            return
        
        logger.info(
            "users_found_for_cleanup",
            count=len(users),
            retention_days=retention_days
        )
        
        total_stats = {
            "users_processed": 0,
            "total_chats_deleted": 0,
            "total_messages_deleted": 0,
            "total_credits_deleted": 0
        }
        
        for user in users:
            stats = await cleanup_user_data(db, user.id, dry_run)
            
            total_stats["users_processed"] += 1
            total_stats["total_chats_deleted"] += stats["chats_deleted"]
            total_stats["total_messages_deleted"] += stats["messages_deleted"]
            total_stats["total_credits_deleted"] += stats["credits_deleted"]
            
            logger.info(
                "user_cleaned_up" if not dry_run else "user_cleanup_preview",
                user_id=user.id,
                deleted_at=user.deleted_at.isoformat() if user.deleted_at else None,
                **stats
            )
        
        logger.info(
            "cleanup_completed" if not dry_run else "cleanup_dry_run_completed",
            **total_stats
        )
        
        print("\n" + "="*60)
        print(f"{'DRY RUN - ' if dry_run else ''}Cleanup Summary")
        print("="*60)
        print(f"Users processed: {total_stats['users_processed']}")
        print(f"Chats deleted: {total_stats['total_chats_deleted']}")
        print(f"Messages deleted: {total_stats['total_messages_deleted']}")
        print(f"Credits deleted: {total_stats['total_credits_deleted']}")
        print("="*60)
        print("\nNOTE: Payment and wallet records are NEVER deleted (compliance).")
        print("="*60 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cleanup soft-deleted users")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only, don't actually delete"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Retention period in days (default: 30)"
    )
    
    args = parser.parse_args()
    
    asyncio.run(main(dry_run=args.dry_run, retention_days=args.days))


'''
# User Cleanup Scripts

## cleanup_deleted_users.py

Permanently deletes data for users who soft-deleted their accounts more than 30 days ago.

### What it deletes:
- ✓ Chat sessions
- ✓ Messages
- ✓ Message credits

### What it NEVER deletes (compliance):
- ✗ Payment records
- ✗ Wallet records
- ✗ Wallet transactions
- ✗ User row (kept with anonymized data)

### Usage:

**Dry run (preview only):**
```bash
python scripts/cleanup_deleted_users.py --dry-run'''