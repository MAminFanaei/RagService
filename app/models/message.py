from sqlalchemy import Column, String, Text, Integer, DateTime, ForeignKey, Enum, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum

from app.core.database import Base


class MessageRole(str, enum.Enum):
    """Message role types"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


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
    meta_data = Column("metadata", JSON , nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    # Relationships
    chat_session = relationship("ChatSession", back_populates="messages")
    
    def __repr__(self):
        return f"<Message(id={self.id}, role={self.role}, chat_id={self.chat_session_id})>"