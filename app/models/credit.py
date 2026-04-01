"""
Message Credit model.

One row per user. user_id is the primary key (no extra UUID).
Lazy-created on first access with FREE_MESSAGES_FOR_NEW_USERS.
"""

from sqlalchemy import Column, String, BigInteger, Integer, DateTime, ForeignKey
from datetime import datetime, timezone

from app.core.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class MessageCredit(Base):
    __tablename__ = "message_credits"

    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    remaining = Column(BigInteger, nullable=False, default=0)
    total_purchased = Column(BigInteger, nullable=False, default=0)
    total_used = Column(BigInteger, nullable=False, default=0)
    rejected_count = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)