# app/services/memory_service.py
"""
Conversation Memory Service

Handles loading, caching, and formatting conversation history
for the RAG engine.
"""

from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass
from datetime import datetime
import json
import hashlib

from sqlalchemy.orm import Session
from sqlalchemy import desc
import redis.asyncio as aioredis

from app.models.message import Message, MessageRole
from app.models.chat import ChatSession
from app.config import settings

# Debug mode
DEBUG = settings.DEBUG


@dataclass
class ConversationMessage:
    """Structured representation of a conversation message"""
    role: str  # "user", "assistant", "system"
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
        """Create from SQLAlchemy Message model"""
        return cls(
            role=msg.role.value if hasattr(msg.role, 'value') else msg.role,
            content=msg.content,
            created_at=msg.created_at,
            metadata=msg.meta_data
        )


@dataclass
class ConversationContext:
    """Full conversation context for RAG processing"""
    chat_id: str
    user_id: str
    messages: List[ConversationMessage]
    total_message_count: int
    is_summarized: bool = False
    summary: Optional[str] = None
    
    @property
    def turn_count(self) -> int:
        """Number of conversation turns (user-assistant pairs)"""
        return len([m for m in self.messages if m.role == "user"])
    
    @property
    def has_history(self) -> bool:
        """Check if there's any conversation history"""
        return len(self.messages) > 0
    
    def get_formatted_history(
        self,
        max_messages: Optional[int] = None,
        include_metadata: bool = False
    ) -> str:
        """
        Format conversation history as a string for prompts.
        
        Args:
            max_messages: Limit number of messages (None = use all)
            include_metadata: Include message metadata in output
            
        Returns:
            Formatted conversation string
        """
        messages_to_format = self.messages
        if max_messages:
            messages_to_format = self.messages[-max_messages:]
        
        if not messages_to_format:
            return ""
        
        formatted_parts = []
        
        # Add summary if available
        if self.is_summarized and self.summary:
            formatted_parts.append(f"[Previous Conversation Summary]\n{self.summary}\n")
        
        # Format each message
        for msg in messages_to_format:
            role_label = "User" if msg.role == "user" else "Assistant"
            formatted_parts.append(f"{role_label}: {msg.content}")
            
            if include_metadata and msg.metadata:
                # Only include key metadata, not full docs
                if "enhanced_query" in msg.metadata:
                    formatted_parts.append(f"  [Enhanced: {msg.metadata['enhanced_query'][:100]}...]")
        
        return "\n\n".join(formatted_parts)
    
    def get_recent_context(self, n_turns: int = 2) -> str:
        """Get just the last N conversation turns for quick context"""
        # Each turn = 2 messages (user + assistant)
        n_messages = n_turns * 2
        return self.get_formatted_history(max_messages=n_messages)


class ConversationMemoryService:
    """
    Service for managing conversation memory.
    
    Responsibilities:
    - Load conversation history from MySQL
    - Cache active conversations in Redis
    - Format history for LLM prompts
    - Handle context window management
    - Optional: Summarize long conversations
    """
    
    # Redis key prefixes
    CACHE_PREFIX = "conv_memory:"
    SUMMARY_PREFIX = "conv_summary:"
    
    def __init__(self):
        self.max_messages = settings.MEMORY_MAX_MESSAGES
        self.max_tokens = settings.MEMORY_MAX_TOKENS
        self.use_redis = settings.MEMORY_USE_REDIS_CACHE
        self.redis_ttl = settings.MEMORY_REDIS_TTL

    # =========================================================================
    # MAIN PUBLIC METHODS
    # =========================================================================
    
    async def get_conversation_context(
        self,
        db: Session,
        chat_id: str,
        user_id: str,
        redis_client: Optional[aioredis.Redis] = None,
        exclude_last_n: int = 0
    ) -> ConversationContext:
        """
        Get conversation context for a chat session.
        
        This is the main method called by RAGService before processing a query.
        
        Args:
            db: Database session
            chat_id: Chat session ID
            user_id: User ID (for verification)
            redis_client: Redis client for caching
            exclude_last_n: Exclude last N messages (useful if current message not saved yet)
            
        Returns:
            ConversationContext with formatted history
        """
        # Try Redis cache first
        if self.use_redis and redis_client:
            cached = await self._get_from_cache(redis_client, chat_id)
            if cached:
                print(f"🔵 Memory: Cache hit for chat {chat_id[:8]}") if DEBUG else None
                return cached
        
        # Load from database
        context = await self._load_from_database(
            db=db,
            chat_id=chat_id,
            user_id=user_id,
            exclude_last_n=exclude_last_n
        )
        
        # Cache the result
        if self.use_redis and redis_client and context.has_history:
            await self._save_to_cache(redis_client, chat_id, context)
        
        print(f"🔵 Memory: Loaded {len(context.messages)} messages for chat {chat_id[:8]}") if DEBUG else None
        
        return context
    
    async def update_context_cache(
        self,
        redis_client: aioredis.Redis,
        chat_id: str,
        new_user_message: str,
        new_assistant_message: str,
        assistant_metadata: Optional[Dict] = None
    ) -> None:
        """
        Update the cached conversation context with new messages.
        
        Call this after saving messages to MySQL to keep cache in sync.
        """
        if not self.use_redis:
            return
        
        try:
            cache_key = f"{self.CACHE_PREFIX}{chat_id}"
            cached_data = await redis_client.get(cache_key)
            
            if cached_data:
                context_dict = json.loads(cached_data)
                messages = context_dict.get("messages", [])
                
                # Add new messages
                now = datetime.utcnow().isoformat()
                messages.append({
                    "role": "user",
                    "content": new_user_message,
                    "created_at": now,
                    "metadata": None
                })
                messages.append({
                    "role": "assistant",
                    "content": new_assistant_message,
                    "created_at": now,
                    "metadata": assistant_metadata
                })
                
                # Trim to max messages
                if len(messages) > self.max_messages:
                    messages = messages[-self.max_messages:]
                
                context_dict["messages"] = messages
                context_dict["total_message_count"] = context_dict.get("total_message_count", 0) + 2
                
                await redis_client.set(
                    cache_key,
                    json.dumps(context_dict),
                    ex=self.redis_ttl
                )
                
                print(f"🔵 Memory: Updated cache for chat {chat_id[:8]}") if DEBUG else None
        
        except Exception as e:
            print(f"⚠️ Memory: Cache update failed: {e}") if DEBUG else None
    
    async def invalidate_cache(
        self,
        redis_client: aioredis.Redis,
        chat_id: str
    ) -> None:
        """Invalidate cached conversation context."""
        if not self.use_redis:
            return
        
        try:
            await redis_client.delete(f"{self.CACHE_PREFIX}{chat_id}")
            await redis_client.delete(f"{self.SUMMARY_PREFIX}{chat_id}")
            print(f"🔵 Memory: Invalidated cache for chat {chat_id[:8]}") if DEBUG else None
        except Exception as e:
            print(f"⚠️ Memory: Cache invalidation failed: {e}") if DEBUG else None
    
    # =========================================================================
    # DATABASE OPERATIONS
    # =========================================================================
    
    async def _load_from_database(
        self,
        db: Session,
        chat_id: str,
        user_id: str,
        exclude_last_n: int = 0
    ) -> ConversationContext:
        """Load conversation history from MySQL."""
        
        # Verify chat exists and belongs to user
        chat = db.query(ChatSession).filter(
            ChatSession.id == chat_id,
            ChatSession.user_id == user_id,
            ChatSession.is_deleted == False
        ).first()
        
        if not chat:
            return ConversationContext(
                chat_id=chat_id,
                user_id=user_id,
                messages=[],
                total_message_count=0
            )
        
        # Get total message count
        from sqlalchemy import func
        total_count = db.query(func.count(Message.id)).filter(
            Message.chat_session_id == chat_id
        ).scalar() or 0
        
        # Load recent messages
        # We load a bit more than max_messages in case we need to exclude some
        query_limit = self.max_messages + exclude_last_n + 2
        
        messages_query = db.query(Message).filter(
            Message.chat_session_id == chat_id
        ).order_by(desc(Message.order_index)).limit(query_limit)
        
        db_messages = messages_query.all()
        
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
    
    # =========================================================================
    # REDIS CACHE OPERATIONS
    # =========================================================================
    
    async def _get_from_cache(
        self,
        redis_client: aioredis.Redis,
        chat_id: str
    ) -> Optional[ConversationContext]:
        """Try to get conversation context from Redis cache."""
        try:
            cache_key = f"{self.CACHE_PREFIX}{chat_id}"
            cached_data = await redis_client.get(cache_key)
            
            if not cached_data:
                return None
            
            data = json.loads(cached_data)
            
            # Reconstruct ConversationContext
            messages = [
                ConversationMessage(
                    role=m["role"],
                    content=m["content"],
                    created_at=datetime.fromisoformat(m["created_at"]) if m.get("created_at") else None,
                    metadata=m.get("metadata")
                )
                for m in data.get("messages", [])
            ]
            
            return ConversationContext(
                chat_id=data["chat_id"],
                user_id=data["user_id"],
                messages=messages,
                total_message_count=data.get("total_message_count", len(messages)),
                is_summarized=data.get("is_summarized", False),
                summary=data.get("summary")
            )
        
        except Exception as e:
            print(f"⚠️ Memory: Cache read failed: {e}") if DEBUG else None
            return None
    
    async def _save_to_cache(
        self,
        redis_client: aioredis.Redis,
        chat_id: str,
        context: ConversationContext
    ) -> None:
        """Save conversation context to Redis cache."""
        try:
            cache_key = f"{self.CACHE_PREFIX}{chat_id}"
            
            # Serialize context
            data = {
                "chat_id": context.chat_id,
                "user_id": context.user_id,
                "messages": [m.to_dict() for m in context.messages],
                "total_message_count": context.total_message_count,
                "is_summarized": context.is_summarized,
                "summary": context.summary,
                "cached_at": datetime.utcnow().isoformat()
            }
            
            await redis_client.set(
                cache_key,
                json.dumps(data),
                ex=self.redis_ttl
            )
        
        except Exception as e:
            print(f"⚠️ Memory: Cache write failed: {e}") if DEBUG else None
    
    # =========================================================================
    # UTILITY METHODS
    # =========================================================================
    
    def estimate_token_count(self, text: str) -> int:
        """
        Rough estimation of token count.
        More accurate would use tiktoken, but this is faster.
        """
        # Rough estimate: 1 token ≈ 4 characters for English
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
        
        # Work backwards from most recent
        for msg in reversed(messages):
            msg_tokens = self.estimate_token_count(msg.content)
            if total_tokens + msg_tokens > max_tokens:
                break
            result.insert(0, msg)
            total_tokens += msg_tokens
        
        return result

    
    def format_for_answer_generation(
        self,
        context: ConversationContext
    ) -> str:
        """
        Format conversation context for answer generation.
        
        Uses fuller format, includes more context.
        """
        if not context.has_history:
            return ""
        
        # Truncate to token limit
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