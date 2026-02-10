# app/services/rag_service.py
"""
RAG Service with Conversation Memory - Async Version

Orchestrates RAG queries with conversation context.
All database operations are now truly async.
"""

import time
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from app.exceptions import BadRequestException
from app.services.chat_service import ChatService
from app.services.memory_service import memory_service, ConversationContext
from app.models.message import MessageRole
from app.config import settings

logger = structlog.get_logger()
DEBUG = settings.DEBUG


class RAGService:
    """Business logic for RAG operations - Async version."""
    
    @staticmethod
    async def process_query(
        db: AsyncSession,
        chat_id: str,
        user_id: str,
        question: str,
        rag_engine
    ) -> Dict[str, Any]:
        """
        Process a RAG query with conversation memory.
        
        All database operations are now async.
        """
        # Verify chat ownership - ASYNC
        chat = await ChatService.get_chat(db, chat_id, user_id)
        if not chat:
            raise BadRequestException("Chat not found or unauthorized")
        
        start_time = time.time()
        
        # =====================================================================
        # STEP 1: Load Conversation History - ASYNC
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
                context: ConversationContext = await memory_service.get_conversation_context(
                    db=db,
                    chat_id=chat_id,
                    user_id=user_id,
                    exclude_last_n=0
                )
                
                if context.has_history:
                    conversation_history = memory_service.format_for_answer_generation(context)
                    
                    context_info.update({
                        "history_loaded": True,
                        "messages_in_context": len(context.messages),
                        "total_messages_in_chat": context.total_message_count,
                        "turns_in_context": context.turn_count
                    })
                    
                    if DEBUG:
                        logger.info(f"RAGService: Loaded {len(context.messages)} messages for context")
                
            except Exception as e:
                if DEBUG:
                    logger.info(f"RAGService: Failed to load conversation history: {e}")
        
        # =====================================================================
        # STEP 2: Execute RAG Query with History (already async)
        # =====================================================================
        rag_result = await rag_engine.query(
            question=question,
            conversation_history=conversation_history
        )
        
        processing_time = (time.time() - start_time) * 1000
        
        # =====================================================================
        # STEP 3: Save Messages to Database - ASYNC
        # =====================================================================
        user_message = await ChatService.add_message(
            db=db,
            chat_id=chat_id,
            role=MessageRole.USER,
            content=question,
            usage=None,
            metadata=None
        )
        
        assistant_metadata = {
            "enhanced_query": rag_result.get("enhanced_query"),
            "docs_retrieved": len(rag_result.get("retrieved_docs", [])),
            "processing_time_ms": processing_time,
            "had_conversation_context": rag_result.get("had_conversation_context", False),
            "context_messages_used": context_info.get("messages_in_context", 0),
            "retrieved_docs": [
                {
                    "content_preview": doc["content"][:500],
                    "metadata": dict(doc["metadata"]) if doc.get("metadata") else {}
                }
                for doc in rag_result.get("retrieved_docs", [])[:5]
            ]
        }
        
        assistant_message = await ChatService.add_message(
            db=db,
            chat_id=chat_id,
            role=MessageRole.ASSISTANT,
            content=rag_result.get("answer", "I don't know"),
            usage=rag_result.get("usage"),
            metadata=assistant_metadata
        )
        
        # =====================================================================
        # STEP 4: Auto-title and Return - ASYNC
        # =====================================================================
        if user_message.order_index == 1:
            auto_title = ChatService.auto_generate_title(question)
            await ChatService.update_chat_title(db, chat_id, user_id, auto_title)
        
        return {
            "user_message": user_message,
            "assistant_message": assistant_message,
            "processing_time_ms": processing_time,
            "rag_metadata": assistant_metadata,
            "context_info": context_info
        }
    
    @staticmethod
    def get_rag_stats() -> Dict[str, Any]:
        """Get RAG engine statistics."""
        from fastapi import Request
        try:
            stats = Request.app.state.rag_engine.get_stats()
            stats["status"] = "healthy"
            return stats
        except Exception as e:
            logger.error("Failed to get RAG stats", error=str(e))
            return {"status": "error", "error": str(e)}