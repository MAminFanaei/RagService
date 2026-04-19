from sqlalchemy import Column, String, Boolean, Integer, DateTime, Enum
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid
import enum

from app.core.database import Base


class AuthProvider(str, enum.Enum):
    """Authentication provider types"""
    LOCAL = "local"
    GOOGLE = "google"
    GITHUB = "github"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Authentication
    username = Column(String(50), unique=True, nullable=True, index=True)
    email = Column(String(255), unique=True, nullable=True, index=True)
    phone_number = Column(String(20), unique=True, nullable=True, index=True)
    hashed_password = Column(String(255), nullable=True)

    # OAuth
    auth_provider = Column(Enum(AuthProvider), default=AuthProvider.LOCAL, nullable=False)
    oauth_id = Column(String(255), nullable=True, unique=True, index=True)

    # Profile
    full_name = Column(String(255), nullable=True)
    avatar_url = Column(String(512), nullable=True)

    # Status
    is_active = Column(Boolean, default=True, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)

    # Rate limiting & quotas
    max_messages_per_day = Column(Integer, nullable=True)  # NULL = use default
    rate_limit_per_minute = Column(Integer, nullable=True)  # NULL = use default

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    # Relationships
    chat_sessions = relationship("ChatSession", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User(id={self.id}, email={self.email}, phone={self.phone_number}, provider={self.auth_provider})>"