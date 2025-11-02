import time
from typing import Dict, Any
from sqlalchemy.orm import Session

from app.core.rag_engine import rag_engine
from app.services.chat_service import ChatService
from app.models.message import MessageRole


class RAGService:
    """Business logic for RAG operations"""
    
    @staticmethod
    async def process_query(
        db: Session,
        chat_id: str,
        user_id: str,
        question: str
    ) -> Dict[str, Any]:
        """
        Process a RAG query and save messages
        
        Returns:
            {
                "user_message": Message,
                "assistant_message": Message,
                "processing_time_ms": float,
                "rag_metadata": dict
            }
        """
        # Verify chat ownership
        chat = ChatService.get_chat(db, chat_id, user_id)
        if not chat:
            raise ValueError("Chat not found or unauthorized")
        
        # Start timing
        start_time = time.time()
        
        # Execute RAG query
        rag_result = await rag_engine.query(question)
        
        processing_time = (time.time() - start_time) * 1000  # Convert to ms
        
        # Save user message
        user_message = ChatService.add_message(
            db=db,
            chat_id=chat_id,
            role=MessageRole.USER,
            content=question,
            metadata=None
        )
        
        # Prepare assistant message metadata
        assistant_metadata = {
            "enhanced_query": rag_result.get("enhanced_query"),
            "docs_retrieved": len(rag_result.get("retrieved_docs", [])),
            "processing_time_ms": processing_time,
            "retrieved_docs": [
                {
                    "content_preview": doc["content"],
                    "metadata": doc["metadata"]
                }
                for doc in rag_result.get("retrieved_docs", [])  # Store top 3 only
            ]
        }
        
        # Save assistant message
        assistant_message = ChatService.add_message(
            db=db,
            chat_id=chat_id,
            role=MessageRole.ASSISTANT,
            content=rag_result.get("answer", "I don't know"),
            metadata=assistant_metadata
        )
        
        # Auto-update chat title if this is the first message
        if user_message.order_index == 1:
            auto_title = ChatService.auto_generate_title(question)
            ChatService.update_chat_title(db, chat_id, user_id, auto_title)
        
        return {
            "user_message": user_message,
            "assistant_message": assistant_message,
            "processing_time_ms": processing_time,
            "rag_metadata": assistant_metadata
        }
    
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