from sqlalchemy import Column, String, Text, Integer, DateTime, ForeignKey, Enum, JSON
from sqlalchemy.orm import relationship
from datetime import datetime, timedelta, timezone
import uuid
import enum
import json

from app.core.database import Base


class MessageRole(str, enum.Enum):
    """Message role types"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class Message(Base):
    __tablename__ = "messages"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    chat_session_id = Column(String(36), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Content
    role = Column(Enum(MessageRole), nullable=False)
    content = Column(Text, nullable=False)
    
    # Ordering (ensures correct sequence even with DB race conditions)
    order_index = Column(Integer, nullable=False)
    
    # RAG Metadata (JSONB stores: enhanced_query, docs_retrieved, confidence, etc.)
    # metadata = Column(JSON, nullable=True)
    meta_data = Column("metadata", JSON , nullable=True) # 🔴 DO NOT CHANGE THIS AT ANY COST , sqlalchemi will fail !!!!!!!!!!!!!!!!
    usage = Column(JSON, nullable=True)
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    
    # Relationships
    chat_session = relationship("ChatSession", back_populates="messages")
    
    def to_response_dict(self, include_metadata: bool = False) -> dict:
        """
        Convert to dictionary for API response.
        
        Args:
            include_metadata: If True, include full metadata (for admins).
                            If False, return empty dict (for normal users).
        """
        return {
            "id": self.id,
            "chat_session_id": self.chat_session_id,
            "role": self.role.value if hasattr(self.role, 'value') else self.role,
            "content": self.content,
            "usage": self.usage,
            "order_index": self.order_index,
            "metadata": self.meta_data if include_metadata else {},
            "created_at": self.created_at
        }

    def __repr__(self):
        return f"<Message(id={self.id}, role={self.role}, chat_id={self.chat_session_id})>"