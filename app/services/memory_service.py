# app/services/memory_service.py
"""
Conversation Memory Service - Async Version

Loads conversation history directly from MySQL using async queries.
"""

from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func

from app.models.message import Message
from app.models.chat import ChatSession
from app.config import settings
import structlog

logger = structlog.get_logger()
DEBUG = settings.DEBUG


@dataclass
class ConversationMessage:
    """Structured representation of a conversation message."""
    role: str
    content: str
    created_at: datetime
    metadata: Optional[Dict] = None
    
    def to_dict(self) -> Dict:
        return {
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "metadata": self.metadata
        }
    
    @classmethod
    def from_db_message(cls, msg: Message) -> "ConversationMessage":
        """Create from SQLAlchemy Message model."""
        return cls(
            role=msg.role.value if hasattr(msg.role, 'value') else msg.role,
            content=msg.content,
            created_at=msg.created_at,
            metadata=msg.meta_data
        )


@dataclass
class ConversationContext:
    """Full conversation context for RAG processing."""
    chat_id: str
    user_id: str
    messages: List[ConversationMessage]
    total_message_count: int
    
    @property
    def turn_count(self) -> int:
        return len([m for m in self.messages if m.role == "user"])
    
    @property
    def has_history(self) -> bool:
        return len(self.messages) > 0
    
    def get_formatted_history(self, max_messages: Optional[int] = None) -> str:
        messages_to_format = self.messages
        if max_messages:
            messages_to_format = self.messages[-max_messages:]
        
        if not messages_to_format:
            return ""
        
        formatted_parts = []
        for msg in messages_to_format:
            role_label = "User" if msg.role == "user" else "Assistant"
            formatted_parts.append(f"{role_label}: {msg.content}")
        
        return "\n\n".join(formatted_parts)
    
    def get_recent_context(self, n_turns: int = 2) -> str:
        n_messages = n_turns * 2
        return self.get_formatted_history(max_messages=n_messages)


class ConversationMemoryService:
    """
    Service for managing conversation memory - Async version.
    
    All database operations are truly async now.
    """
    
    def __init__(self):
        self.max_messages = settings.MEMORY_MAX_MESSAGES
        self.max_tokens = settings.MEMORY_MAX_TOKENS

    async def get_conversation_context(
        self,
        db: AsyncSession,
        chat_id: str,
        user_id: str,
        exclude_last_n: int = 0
    ) -> ConversationContext:
        """Get conversation context for a chat session."""
        context = await self._load_from_database(
            db=db,
            chat_id=chat_id,
            user_id=user_id,
            exclude_last_n=exclude_last_n
        )
        
        if DEBUG:
            logger.info(f"Memory: Loaded {len(context.messages)} messages for chat {chat_id[:8]}")
        
        return context

    async def _load_from_database(
        self,
        db: AsyncSession,
        chat_id: str,
        user_id: str,
        exclude_last_n: int = 0
    ) -> ConversationContext:
        """Load conversation history from MySQL - Async."""
        
        # Verify chat exists and belongs to user
        result = await db.execute(
            select(ChatSession).where(
                ChatSession.id == chat_id,
                ChatSession.user_id == user_id,
                ChatSession.is_deleted == False
            )
        )
        chat = result.scalar_one_or_none()
        
        if not chat:
            return ConversationContext(
                chat_id=chat_id,
                user_id=user_id,
                messages=[],
                total_message_count=0
            )
        
        # Get total message count
        count_result = await db.execute(
            select(func.count()).select_from(Message).where(
                Message.chat_session_id == chat_id
            )
        )
        total_count = count_result.scalar() or 0
        
        # Load recent messages
        query_limit = self.max_messages + exclude_last_n + 2
        
        result = await db.execute(
            select(Message).where(
                Message.chat_session_id == chat_id
            ).order_by(desc(Message.order_index)).limit(query_limit)
        )
        db_messages = list(result.scalars().all())
        
        # Reverse to get chronological order
        db_messages = list(reversed(db_messages))
        
        # Exclude last N if requested
        if exclude_last_n > 0 and len(db_messages) > exclude_last_n:
            db_messages = db_messages[:-exclude_last_n]
        
        # Trim to max messages
        if len(db_messages) > self.max_messages:
            db_messages = db_messages[-self.max_messages:]
        
        # Convert to ConversationMessage objects
        messages = [
            ConversationMessage.from_db_message(msg)
            for msg in db_messages
        ]
        
        return ConversationContext(
            chat_id=chat_id,
            user_id=user_id,
            messages=messages,
            total_message_count=total_count
        )

    def estimate_token_count(self, text: str) -> int:
        """Rough estimation of token count (1 token ≈ 4 chars)."""
        return len(text) // 4
    
    def truncate_to_token_limit(
        self,
        messages: List[ConversationMessage],
        max_tokens: int
    ) -> List[ConversationMessage]:
        """Truncate messages to fit within token limit."""
        if not messages:
            return []
        
        result = []
        total_tokens = 0
        
        for msg in reversed(messages):
            msg_tokens = self.estimate_token_count(msg.content)
            if total_tokens + msg_tokens > max_tokens:
                break
            result.insert(0, msg)
            total_tokens += msg_tokens
        
        return result

    def format_for_answer_generation(self, context: ConversationContext) -> str:
        """Format conversation context for answer generation."""
        if not context.has_history:
            return ""
        
        messages = self.truncate_to_token_limit(
            context.messages,
            self.max_tokens
        )
        
        parts = []
        for msg in messages:
            role = "User" if msg.role == "user" else "Assistant"
            parts.append(f"{role}: {msg.content}")
        
        return "\n\n".join(parts)


# Singleton instance
memory_service = ConversationMemoryService()