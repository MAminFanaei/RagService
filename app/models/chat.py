from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime, timedelta, timezone
import uuid

from app.core.database import Base

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class ChatSession(Base):
    __tablename__ = "chat_sessions"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Metadata
    title = Column(String(255), nullable=False, default="New Chat")
    
    # Soft delete (chats are never hard deleted)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    
    # Relationships
    user = relationship("User", back_populates="chat_sessions")
    messages = relationship("Message", back_populates="chat_session", cascade="all, delete-orphan", order_by="Message.order_index")
    
    def __repr__(self):
        return f"<ChatSession(id={self.id}, user_id={self.user_id}, title={self.title})>"