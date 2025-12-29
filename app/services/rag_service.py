# app/services/rag_service.py
"""
RAG Service with Conversation Memory

Orchestrates RAG queries with conversation context.
"""

import time
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
import redis.asyncio as aioredis

from app.core.rag_engine import rag_engine
from app.services.chat_service import ChatService
from app.services.memory_service import memory_service, ConversationContext
from app.models.message import MessageRole
from app.config import settings


DEBUG = settings.DEBUG


class RAGService:
    """
    Business logic for RAG operations.
    
    Now includes conversation memory integration:
    1. Loads conversation history before processing
    2. Passes history to RAG engine
    3. Updates cache after processing
    """
    
    @staticmethod
    async def process_query(
        db: Session,
        chat_id: str,
        user_id: str,
        question: str,
        redis_client: Optional[aioredis.Redis] = None
    ) -> Dict[str, Any]:
        """
        Process a RAG query with conversation memory.
        
        Args:
            db: Database session
            chat_id: Chat session ID
            user_id: User ID
            question: User's question
            redis_client: Redis client for caching (optional but recommended)
            
        Returns:
            {
                "user_message": Message,
                "assistant_message": Message,
                "processing_time_ms": float,
                "rag_metadata": dict,
                "context_info": dict  # NEW: info about conversation context used
            }
        """
        # Verify chat ownership
        chat = ChatService.get_chat(db, chat_id, user_id)
        if not chat:
            raise ValueError("Chat not found or unauthorized")
        
        # Start timing
        start_time = time.time()
        
        # =====================================================================
        # STEP 1: Load Conversation History (NEW)
        # =====================================================================
        conversation_history = ""
        context_info = {
            "memory_enabled": settings.ENABLE_CONVERSATION_MEMORY,
            "history_loaded": False,
            "messages_in_context": 0,
            "total_messages_in_chat": 0
        }
        
        if settings.ENABLE_CONVERSATION_MEMORY:
            try:
                # Load conversation context
                # Note: We don't exclude any messages since current question isn't saved yet
                context: ConversationContext = await memory_service.get_conversation_context(
                    db=db,
                    chat_id=chat_id,
                    user_id=user_id,
                    redis_client=redis_client,
                    exclude_last_n=0
                )
                
                if context.has_history:
                    # Format for answer generation (fuller context)
                    conversation_history = memory_service.format_for_answer_generation(context)
                    
                    context_info.update({
                        "history_loaded": True,
                        "messages_in_context": len(context.messages),
                        "total_messages_in_chat": context.total_message_count,
                        "turns_in_context": context.turn_count
                    })
                    
                    print(f"🔵 RAGService: Loaded {len(context.messages)} messages for context") if DEBUG else None
                
            except Exception as e:
                print(f"⚠️ RAGService: Failed to load conversation history: {e}") if DEBUG else None
                # Continue without history - graceful degradation
        
        # =====================================================================
        # STEP 2: Execute RAG Query with History
        # =====================================================================
        rag_result = await rag_engine.query(
            question=question,
            conversation_history=conversation_history
        )
        
        processing_time = (time.time() - start_time) * 1000
        
        # =====================================================================
        # STEP 3: Save Messages to Database
        # =====================================================================
        
        # Save user message
        user_message = ChatService.add_message(
            db=db,
            chat_id=chat_id,
            role=MessageRole.USER,
            content=question,
            usage=None,
            metadata=None
        )
        
        # Prepare assistant message metadata
        assistant_metadata = {
            "enhanced_query": rag_result.get("enhanced_query"),
            "docs_retrieved": len(rag_result.get("retrieved_docs", [])),
            "processing_time_ms": processing_time,
            "had_conversation_context": rag_result.get("had_conversation_context", False),
            "context_messages_used": context_info.get("messages_in_context", 0),
            "retrieved_docs": [
                {
                    "content_preview": doc["content"][:500],  # Limit preview size
                    "metadata": dict(doc["metadata"]) if doc.get("metadata") else {}
                }
                for doc in rag_result.get("retrieved_docs", [])[:5]  # Store top 5 only
            ]
        }
        
        # Save assistant message
        assistant_message = ChatService.add_message(
            db=db,
            chat_id=chat_id,
            role=MessageRole.ASSISTANT,
            content=rag_result.get("answer", "I don't know"),
            usage=rag_result.get("usage"),
            metadata=assistant_metadata
        )
        
        # =====================================================================
        # STEP 4: Update Cache (NEW)
        # =====================================================================
        if settings.ENABLE_CONVERSATION_MEMORY and redis_client:
            try:
                await memory_service.update_context_cache(
                    redis_client=redis_client,
                    chat_id=chat_id,
                    new_user_message=question,
                    new_assistant_message=rag_result.get("answer", ""),
                    assistant_metadata={"enhanced_query": rag_result.get("enhanced_query")}
                )
            except Exception as e:
                print(f"⚠️ RAGService: Failed to update cache: {e}") if DEBUG else None
        
        # =====================================================================
        # STEP 5: Auto-title and Return
        # =====================================================================
        
        # Auto-update chat title if this is the first message
        if user_message.order_index == 1:
            auto_title = ChatService.auto_generate_title(question)
            ChatService.update_chat_title(db, chat_id, user_id, auto_title)
        
        return {
            "user_message": user_message,
            "assistant_message": assistant_message,
            "processing_time_ms": processing_time,
            "rag_metadata": assistant_metadata,
            "context_info": context_info  # NEW
        }
    
    @staticmethod
    async def clear_conversation_cache(
        chat_id: str,
        redis_client: aioredis.Redis
    ) -> bool:
        """
        Clear the conversation cache for a chat.
        
        Call this when:
        - Chat is deleted
        - User requests to "forget" conversation
        - Cache needs manual refresh
        """
        try:
            await memory_service.invalidate_cache(redis_client, chat_id)
            return True
        except Exception as e:
            print(f"⚠️ Failed to clear cache: {e}")
            return False
    
    @staticmethod
    def get_rag_stats() -> Dict[str, Any]:
        """Get RAG engine statistics"""
        try:
            stats = rag_engine.get_stats()
            stats["status"] = "healthy"
            return stats
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }