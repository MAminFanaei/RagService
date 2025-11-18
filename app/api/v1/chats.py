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
        question=message.content
    )
    processing_time = (time.time() - start_time) * 1000
    
    return {
        "message_id": result["assistant_message"].id,
        "chat_id": chat_id,
        "user_message": result["user_message"],
        "assistant_message": result["assistant_message"],
        "processing_time_ms": processing_time
    }