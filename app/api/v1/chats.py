# app/api/v1/chats.py
"""
Chat API Endpoints - Async Version

All endpoints now use AsyncSession and await service calls.
"""

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.memory_service import memory_service
import time
import redis.asyncio as aioredis
import structlog
from app.core.database import get_db
from app.middleware.exceptions import (
    ForbiddenException, InputTooLongException, InternalException, 
    NotFoundException, RateLimitException, AppException
)
from app.schemas.chat import (
    ChatCreate, ChatUpdate, ChatResponse, ChatWithMessages,
    ChatListResponse, MessageCreate, RAGQueryResponse
)
from app.services.chat_service import ChatService
from app.services.rag_service import RAGService
from app.services.rate_limit_service import RateLimitService
from app.api.deps import get_current_user, get_redis_client
from app.models.user import User
from app.config import settings
from app.services.credit_service import CreditService
from app.payment.services.wallet_service import WalletService
from app.middleware.exceptions import InsufficientCreditsException

router = APIRouter(prefix="/chats", tags=["Chats"])
logger = structlog.get_logger()


@router.post("", response_model=ChatResponse, status_code=status.HTTP_201_CREATED)
async def create_chat(
    chat_data: ChatCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new chat session."""
    chat = await ChatService.create_chat(db, current_user.id, chat_data.title)
    
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
    db: AsyncSession = Depends(get_db)
):
    """List user's chat sessions."""
    return await ChatService.list_user_chats(
        db=db,
        user_id=current_user.id,
        skip=skip,
        limit=limit,
        include_deleted=current_user.is_admin and include_deleted  # Only allow including deleted if admin
    )


@router.get("/{chat_id}", response_model=ChatWithMessages)
async def get_chat(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get a specific chat with all messages."""
    chat = await ChatService.get_chat(db, chat_id, current_user.id)
    if not chat:
        raise NotFoundException("Chat not found")
    
    messages = await ChatService.get_messages(db, chat_id)
    
    cleaned_messages = [
        msg.to_response_dict(include_metadata=current_user.is_admin) 
        for msg in messages
    ]
    
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


@router.patch("/{chat_id}", response_model=ChatResponse)
async def update_chat(
    chat_id: str,
    chat_update: ChatUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update chat title."""
    chat = await ChatService.update_chat_title(
        db=db,
        chat_id=chat_id,
        user_id=current_user.id,
        new_title=chat_update.title
    )
    
    if not chat:
        raise NotFoundException("Chat not found")
    
    messages = await ChatService.get_messages(db, chat_id, limit=1)
    
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


@router.delete("/{chat_id}")
async def delete_chat(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Soft delete a chat."""
    chat = await ChatService.get_chat(db, chat_id, current_user.id)
    if not chat:
        raise NotFoundException("Chat not found")

    if chat.is_deleted:
        raise NotFoundException("Chat not found (maybe deleted)")
    
    success = await ChatService.soft_delete_chat(db, chat_id, current_user.id)
    if not success:
        raise NotFoundException("Operation failed")
    
    return "Chat successfully deleted"


@router.post("/{chat_id}/restore", response_model=ChatResponse)
async def restore_chat(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Restore a soft-deleted chat."""
    if not current_user.is_admin:
        raise ForbiddenException("Admin access required")
    
    success = await ChatService.restore_chat(db, chat_id, current_user.id)
    if not success:
        raise NotFoundException("Chat not found or not deleted")
    
    chat = await ChatService.get_chat(db, chat_id, current_user.id)
    messages = await ChatService.get_messages(db, chat_id, limit=1)
    
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
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis_client)
):
    """Send a message and get RAG response."""
    content_length = len(message.content) // 4
    max_length = settings.USER_QUERY_LENGTH_LIMIT

    if content_length > max_length:
        raise InputTooLongException(
            max_length=max_length,
            actual_length=content_length
        )

    if current_user.is_admin:
        quota_allowed, _, allowed = True, 9999, True
    else:
        rate_per_min, quota_per_day = RateLimitService.get_user_limits(current_user)

        quota_allowed, _ = await RateLimitService.check_daily_quota(
            redis=redis,
            user_id=current_user.id,
            max_per_day=quota_per_day
        )

        allowed = await RateLimitService.check_per_min_rate_limit(
            redis=redis,
            user_id=current_user.id,
            limit_per_minute=rate_per_min,
            key_prefix="rag_query"
        )

        if not quota_allowed:
            raise RateLimitException("Daily message quota exceeded. Will reset at midnight.")

        if not allowed:
            raise RateLimitException("Too many requests. Please try again later.")

        # Credit check 
        credits = await CreditService.get_or_create(db, current_user.id)
        if credits.remaining <= 0:
            wallet = await WalletService.get_or_create_wallet(db, current_user.id)
            raise InsufficientCreditsException(
                message="No remaining messages. Purchase more to continue.",
                data={
                    "remaining_messages": 0,
                    "wallet_balance": wallet.balance,
                    "price_per_message": settings.PRICE_PER_MESSAGE,
                    "affordable_messages": wallet.balance // settings.PRICE_PER_MESSAGE,
                    "action": "purchase_credits",
                },
            )

    # Validate chat exists and belongs to user
    chat = await ChatService.get_chat(db, chat_id, current_user.id)
    if not chat:
        raise NotFoundException("Chat not found")

    try:
        start_time = time.time()
        result = await RAGService.process_query(
            db=db,
            chat_id=chat_id,
            user_id=current_user.id,
            question=message.content,
            rag_engine=request.app.state.rag_engine
        )
        processing_time = (time.time() - start_time) * 1000

    except AppException:
        raise
    except Exception as e:
        logger.error("RAG query failed", error=str(e))
        raise InternalException("Failed to process message")

    # ── Rate limit increment (always, same as before) ──
    await RateLimitService.increment_rate_limit(
        redis=redis,
        user_id=current_user.id,
        key_prefix="rag_query"
    )

    await RateLimitService.increment_daily_quota(
        redis=redis,
        user_id=current_user.id
    )

    # ── Credit deduction (after success, admin bypasses) ──
    credits_remaining = None
    if not current_user.is_admin:
        is_rejected = result.get("is_rejected", False)

        if is_rejected:
            rejection_info = await CreditService.record_rejection(db, current_user.id)
            credits_remaining = rejection_info["credits_remaining"]
        else:
            credits_remaining = await CreditService.consume_one(db, current_user.id)

        await db.commit()

    return {
        "message_id": result["assistant_message"].id,
        "chat_id": chat_id,
        "user_message": result["user_message"].to_response_dict(include_metadata=current_user.is_admin),
        "assistant_message": result["assistant_message"].to_response_dict(include_metadata=current_user.is_admin),
        "processing_time_ms": processing_time,
        "credits_remaining": credits_remaining,
    }


@router.get("/{chat_id}/memory")
async def get_chat_memory(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """[ADMIN ONLY] Get conversation memory for a chat."""
    if not current_user.is_admin:
        raise ForbiddenException("Admin access required")
    
    chat = await ChatService.get_chat_admin(db, chat_id)
    if not chat:
        raise NotFoundException("Chat not found")
    
    
    if not settings.ENABLE_CONVERSATION_MEMORY:
        return {
            "status": "disabled",
            "message": "Conversation memory is disabled in settings",
            "settings": {"ENABLE_CONVERSATION_MEMORY": False}
        }
    
    try:
        context = await memory_service.get_conversation_context(
            db=db,
            chat_id=chat_id,
            user_id=chat.user_id
        )
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to load memory: {str(e)}",
            "chat_id": chat_id
        }
    
    formatted_for_answer = None
    if context.has_history:
        formatted_for_answer = memory_service.format_for_endpoint(context)
    
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
        "formatted_context": {
            "answer_generation": {
                "content": formatted_for_answer,
                "char_count": len(formatted_for_answer) if formatted_for_answer else 0,
                "estimated_tokens": len(formatted_for_answer) // 4 if formatted_for_answer else 0
            }
        },
        "settings": {
            "ENABLE_CONVERSATION_MEMORY": settings.ENABLE_CONVERSATION_MEMORY,
            "MEMORY_MAX_MESSAGES": settings.MEMORY_MAX_MESSAGES,
            "MEMORY_MAX_TOKENS": settings.MEMORY_MAX_TOKENS
        }
    }