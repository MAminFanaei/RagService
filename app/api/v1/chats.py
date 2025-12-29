from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List

from app.core.database import get_db
from app.schemas.chat import (
    ChatCreate, ChatUpdate, ChatResponse, ChatWithMessages,
    ChatListResponse, MessageCreate, RAGQueryResponse
)
from app.services.chat_service import ChatService
from app.services.rag_service import RAGService
from app.services.user_service import UserService
from app.services.rate_limit_service import RateLimitService
from app.api.deps import get_current_user, get_redis_client
from app.models.user import User
from app.config import settings
import redis.asyncio as aioredis
import time

def _clean_metadata(metadata):
    """Force metadata to be a clean dict"""
    if metadata is None:
        return None
    
    # If it's already a dict, return it
    if isinstance(metadata, dict):
        return metadata
    
    # Try to convert to dict
    try:
        import json
        # Test if it's serializable
        json.dumps(metadata)
        return metadata
    except:
        # Return empty dict if can't serialize
        return {}
    
router = APIRouter(prefix="/chats", tags=["Chats"])


@router.post("", response_model=ChatResponse, status_code=status.HTTP_201_CREATED)
async def create_chat(
    chat_data: ChatCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new chat session"""
    chat = ChatService.create_chat(db, current_user.id, chat_data.title)
    
    return {
        "id": chat.id,
        "user_id": chat.user_id,
        "title": chat.title,
        "is_deleted": chat.is_deleted,
        "created_at": chat.created_at,
        "updated_at": chat.updated_at,
        "message_count": 0,
        "last_message_at": None
    }


@router.get("", response_model=ChatListResponse)
async def list_chats(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    include_deleted: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List user's chat sessions"""
    result = ChatService.list_user_chats(
        db=db,
        user_id=current_user.id,
        skip=skip,
        limit=limit,
        include_deleted=include_deleted
    )
    return result


@router.get("/{chat_id}", response_model=ChatWithMessages)
async def get_chat(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a specific chat with all messages"""
    chat = ChatService.get_chat(db, chat_id, current_user.id)
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found"
        )
    
    messages = ChatService.get_messages(db, chat_id)
    
    # CLEAN METADATA BEFORE RETURNING
    cleaned_messages = []
    for msg in messages:
        msg_dict = {
            "id": msg.id,
            "chat_session_id": msg.chat_session_id,
            "role": msg.role.value if hasattr(msg.role, 'value') else msg.role,
            "content": msg.content,
            "order_index": msg.order_index,
            "metadata": _clean_metadata(msg.metadata),  # Clean here!
            "created_at": msg.created_at
        }
        cleaned_messages.append(msg_dict)
    
    return {
        "id": chat.id,
        "user_id": chat.user_id,
        "title": chat.title,
        "is_deleted": chat.is_deleted,
        "created_at": chat.created_at,
        "updated_at": chat.updated_at,
        "message_count": len(cleaned_messages),
        "last_message_at": cleaned_messages[-1]["created_at"] if cleaned_messages else None,
        "messages": cleaned_messages
    }


def _clean_metadata(metadata):
    """Force metadata to be a clean dict"""
    if metadata is None:
        return None
    
    # If it's already a dict, return it
    if isinstance(metadata, dict):
        return metadata
    
    # Try to convert to dict
    try:
        import json
        # Test if it's serializable
        json.dumps(metadata)
        return metadata
    except:
        # Return empty dict if can't serialize
        return {}


@router.patch("/{chat_id}", response_model=ChatResponse)
async def update_chat(
    chat_id: str,
    chat_update: ChatUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update chat title"""
    chat = ChatService.update_chat_title(
        db=db,
        chat_id=chat_id,
        user_id=current_user.id,
        new_title=chat_update.title
    )
    
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found"
        )
    
    messages = ChatService.get_messages(db, chat_id, limit=1)
    
    return {
        "id": chat.id,
        "user_id": chat.user_id,
        "title": chat.title,
        "is_deleted": chat.is_deleted,
        "created_at": chat.created_at,
        "updated_at": chat.updated_at,
        "message_count": len(messages),
        "last_message_at": messages[0].created_at if messages else None
    }


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Soft delete a chat (can be restored)"""
    success = ChatService.soft_delete_chat(db, chat_id, current_user.id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found"
        )
    return None


@router.post("/{chat_id}/restore", response_model=ChatResponse)
async def restore_chat(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Restore a soft-deleted chat"""
    success = ChatService.restore_chat(db, chat_id, current_user.id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found or not deleted"
        )
    
    chat = ChatService.get_chat(db, chat_id, current_user.id)
    messages = ChatService.get_messages(db, chat_id, limit=1)
    
    return {
        "id": chat.id,
        "user_id": chat.user_id,
        "title": chat.title,
        "is_deleted": chat.is_deleted,
        "created_at": chat.created_at,
        "updated_at": chat.updated_at,
        "message_count": len(messages),
        "last_message_at": messages[0].created_at if messages else None
    }


@router.post("/{chat_id}/messages", response_model=RAGQueryResponse)
async def send_message(
    chat_id: str,
    message: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis_client)
):
    """Send a message and get RAG response"""
    
    # Check rate limits
    rate_per_min, quota_per_day = await RateLimitService.get_user_limits(current_user)
    
    # Check per-minute rate limit
    allowed, remaining = await RateLimitService.check_rate_limit(
        redis=redis,
        user_id=current_user.id,
        limit_per_minute=rate_per_min,
        key_prefix="rag_query"
    )
    
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Try again in a minute."
        )
    
    # Check daily quota
    quota_allowed, quota_remaining = await RateLimitService.check_daily_quota(
        redis=redis,
        user_id=current_user.id,
        max_per_day=quota_per_day
    )
    
    if not quota_allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily message quota ({quota_per_day}) exceeded. Resets at midnight."
        )
    
    # Process RAG query
    start_time = time.time()
    result = await RAGService.process_query(
        db=db,
        chat_id=chat_id,
        user_id=current_user.id,
        question=message.content,
        redis_client=redis  # <-- NEW: Pass redis for memory
    )
    processing_time = (time.time() - start_time) * 1000
    
    return {
        "message_id": result["assistant_message"].id,
        "chat_id": chat_id,
        "user_message": result["user_message"],
        "assistant_message": result["assistant_message"],
        "processing_time_ms": processing_time
    }

# Add this endpoint to app/api/v1/chats.py

@router.get("/{chat_id}/memory")
async def get_chat_memory(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis_client)
):
    """
    [ADMIN ONLY] Get conversation memory for a chat.
    
    Shows exactly what context the RAG engine receives when processing queries.
    Useful for debugging and monitoring memory functionality.
    
    Returns:
    - Messages currently in memory
    - Redis cache status
    - Formatted context strings sent to LLM
    """
    # ==========================================
    # ADMIN CHECK
    # ==========================================
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    
    # ==========================================
    # VERIFY CHAT EXISTS
    # ==========================================
    # Admin can view any chat, so we use admin method
    chat = ChatService.get_chat_admin(db, chat_id)
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found"
        )
    
    # ==========================================
    # CHECK SETTINGS
    # ==========================================
    from app.services.memory_service import memory_service
    
    if not settings.ENABLE_CONVERSATION_MEMORY:
        return {
            "status": "disabled",
            "message": "Conversation memory is disabled in settings",
            "settings": {
                "ENABLE_CONVERSATION_MEMORY": False
            }
        }
    
    # ==========================================
    # LOAD MEMORY CONTEXT
    # ==========================================
    try:
        context = await memory_service.get_conversation_context(
            db=db,
            chat_id=chat_id,
            user_id=chat.user_id,  # Use chat's owner, not current admin
            redis_client=redis
        )
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to load memory: {str(e)}",
            "chat_id": chat_id
        }
    
    # ==========================================
    # CHECK REDIS CACHE STATUS
    # ==========================================
    redis_status = {
        "enabled": settings.MEMORY_USE_REDIS_CACHE,
        "cached": False,
        "ttl_seconds": None
    }
    
    if settings.MEMORY_USE_REDIS_CACHE:
        try:
            cache_key = f"conv_memory:{chat_id}"
            cached_data = await redis.get(cache_key)
            ttl = await redis.ttl(cache_key)
            redis_status["cached"] = cached_data is not None
            redis_status["ttl_seconds"] = ttl if ttl > 0 else None
        except Exception:
            redis_status["error"] = "Failed to check Redis"
    
    # ==========================================
    # FORMAT CONTEXT (What LLM Actually Receives)
    # ==========================================
    formatted = {
        "for_query_enhancement": None,
        "for_answer_generation": None
    }
    
    if context.has_history:
        formatted["for_answer_generation"] = memory_service.format_for_answer_generation(context)
    
    # ==========================================
    # BUILD RESPONSE
    # ==========================================
    return {
        "status": "ok",
        "chat_id": chat_id,
        "chat_owner": chat.user_id,
        "chat_title": chat.title,
        
        "memory": {
            "has_history": context.has_history,
            "messages_in_context": len(context.messages),
            "total_messages_in_db": context.total_message_count,
            "conversation_turns": context.turn_count,
        },
        
        "messages": [
            {
                "role": msg.role,
                "content": msg.content,
                "content_length": len(msg.content),
                "created_at": msg.created_at.isoformat() if msg.created_at else None
            }
            for msg in context.messages
        ],
        
        "redis_cache": redis_status,
        
        "formatted_context": {
            "query_enhancement": {
                "content": formatted["for_query_enhancement"],
                "char_count": len(formatted["for_query_enhancement"]) if formatted["for_query_enhancement"] else 0
            },
            "answer_generation": {
                "content": formatted["for_answer_generation"],
                "char_count": len(formatted["for_answer_generation"]) if formatted["for_answer_generation"] else 0
            }
        },
        
        "settings": {
            "ENABLE_CONVERSATION_MEMORY": settings.ENABLE_CONVERSATION_MEMORY,
            "MEMORY_MAX_MESSAGES": settings.MEMORY_MAX_MESSAGES,
            "MEMORY_MAX_TOKENS": settings.MEMORY_MAX_TOKENS,
            "MEMORY_USE_REDIS_CACHE": settings.MEMORY_USE_REDIS_CACHE,
            "MEMORY_REDIS_TTL": settings.MEMORY_REDIS_TTL
        }
    }