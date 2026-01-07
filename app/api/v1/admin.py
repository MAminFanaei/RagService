from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import datetime, timezone , timedelta

from app.core.database import get_db
from app.core.security import verify_password
from app.exceptions import BadRequestException, NotFoundException
from app.schemas.admin import (
    AdminUserUpdate, UserActionResponse, UserDeleteRequest, UserDeleteResponse, UserDisableRequest, UserStatsResponse, SystemStatsResponse,
    RAGStatsResponse, ConversationExport
)
from app.schemas.user import UserWithStats
from app.services.user_service import UserService
from app.services.chat_service import ChatService
from app.services.rag_service import RAGService
from app.api.deps import get_current_admin_user
from app.models.user import User
from app.models.chat import ChatSession
from app.models.message import Message

router = APIRouter(prefix="/admin", tags=["Admin"])



@router.get("/users")
async def list_all_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    include_inactive: bool = Query(True),
    search: Optional[str] = Query(None),
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """[ADMIN ONLY] List all users with their stats."""
    return UserService.get_all_users_admin(
        db=db,
        skip=skip,
        limit=limit,
        include_inactive=include_inactive,
        search=search
    )


@router.get("/users/{user_id}", response_model=UserStatsResponse)
async def get_user_details(
    user_id: str,
    admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Get detailed user information"""
    user = UserService.get_by_id(db, user_id)
    if not user:
        raise NotFoundException("User not found")

    
    stats = UserService.get_user_stats(db, user_id)
    
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "auth_provider": user.auth_provider.value,
        "is_active": user.is_active,
        "is_admin": user.is_admin,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
        "total_chats": stats.get("total_chats", 0),
        "total_messages": stats.get("total_messages", 0),
        "messages_today": stats.get("messages_today", 0),
        "max_messages_per_day": user.max_messages_per_day,
        "rate_limit_per_minute": user.rate_limit_per_minute
    }


@router.patch("/users/{user_id}", response_model=UserStatsResponse)
async def update_user_settings(
    user_id: str,
    updates: AdminUserUpdate,
    admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Update user settings (admin only)"""
    user = UserService.get_by_id(db, user_id)
    if not user:
        raise NotFoundException("User not found")
    
    # Apply updates
    if updates.is_admin is not None:
        user.is_admin = updates.is_admin
    if updates.max_messages_per_day is not None:
        user.max_messages_per_day = updates.max_messages_per_day
    if updates.rate_limit_per_minute is not None:
        user.rate_limit_per_minute = updates.rate_limit_per_minute
    
    db.commit()
    db.refresh(user)
    
    stats = UserService.get_user_stats(db, user_id)
    
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "auth_provider": user.auth_provider.value,
        "is_active": user.is_active,
        "is_admin": user.is_admin,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
        "total_chats": stats.get("total_chats", 0),
        "total_messages": stats.get("total_messages", 0),
        "messages_today": stats.get("messages_today", 0),
        "max_messages_per_day": user.max_messages_per_day,
        "rate_limit_per_minute": user.rate_limit_per_minute
    }


@router.get("/users/{user_id}/conversations", response_model=List[ConversationExport])
async def export_user_conversations(
    user_id: str,
    admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Export all conversations for a user (including deleted)"""
    user = UserService.get_by_id(db, user_id)
    if not user:
        raise NotFoundException("User not found")
    
    # Get all chats (including deleted)
    chats = db.query(ChatSession).filter(
        ChatSession.user_id == user_id
    ).order_by(ChatSession.created_at.desc()).all()
    
    conversations = []
    for chat in chats:
        conversation = ChatService.export_conversation(db, chat.id)
        if conversation:
            conversations.append(conversation)
    
    return conversations


@router.get("/conversations/{chat_id}", response_model=ConversationExport)
async def export_conversation(
    chat_id: str,
    admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Export a specific conversation"""
    conversation = ChatService.export_conversation(db, chat_id)
    if not conversation:
        raise NotFoundException("Conversation not found")
    
    return conversation

@router.get("/stats/user_usage")
async def get_system_stats(
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    [ADMIN ONLY] Get system-wide statistics.
    """
    
    # User stats
    total_users = db.query(func.count(User.id)).scalar()
    active_users = db.query(func.count(User.id)).filter(User.is_active == True).scalar()
    
    # Chat stats
    total_chats = db.query(func.count(ChatSession.id)).scalar()
    
    # Message stats
    total_messages = db.query(func.count(Message.id)).scalar()
    
    # Today's activity
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    messages_today = db.query(func.count(Message.id)).filter(
        Message.created_at >= today_start
    ).scalar()
    
    # Last 7 days active users
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    active_last_week = db.query(func.count(User.id)).filter(
        User.last_login_at >= week_ago
    ).scalar()

    # Average response time (last 1000 messages with metadata)
    recent_messages = db.query(Message).filter(
        Message.meta_data.isnot(None)
    ).order_by(Message.created_at.desc()).limit(1000).all()
    
    avg_response_time = 0
    if recent_messages:
        times = [
            msg.meta_data.get("processing_time_ms", 0)
            for msg in recent_messages
            if msg.meta_data and "processing_time_ms" in msg.meta_data
        ]
        avg_response_time = sum(times) / len(times) if times else 0
    
    return {
        "users": {
            "total": total_users,
            "active": active_users,
            "inactive": total_users - active_users,
            "active_last_7_days": active_last_week
        },
        "chats": {
            "total": total_chats
        },
        "messages": {
            "total": total_messages,
            "today": messages_today,
            "average_response_time_ms": avg_response_time
        }
    }



@router.post("/users/{user_id}/disable", response_model=UserActionResponse)
async def disable_user(
    user_id: str,
    request: UserDisableRequest = None,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    [ADMIN ONLY] Disable a user account.
    
    The user will not be able to login, but their data is preserved.
    """
    if user_id == current_admin.id:
        raise BadRequestException("Cannot disable your own account")
    
    # Service raises appropriate exceptions
    user = UserService.disable_user(db, user_id)
    
    return {
        "message": "User disabled successfully",
        "user_id": user_id,
        "is_active": False
    }


@router.post("/users/{user_id}/enable", response_model=UserActionResponse)
async def enable_user(
    user_id: str,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """[ADMIN ONLY] Re-enable a disabled user account."""
    user = UserService.enable_user(db, user_id)
    
    return {
        "message": "User enabled successfully",
        "user_id": user_id,
        "is_active": True
    }


@router.delete("/users/{user_id}", response_model=UserDeleteResponse)
async def delete_user_permanently(
    user_id: str,
    request: UserDeleteRequest,
    current_admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    [ADMIN ONLY] Permanently delete a user and ALL their data.
    
    ⚠️ WARNING: This action is IRREVERSIBLE!
    
    Requires:
    - Admin's own password for verification
    - User's username must match confirm_username field
    
    Example request body:
    ```json
    {
        "admin_password": "your_admin_password",
        "confirm_username": "username_to_delete"
    }
    ```
    """
    # 1. Cannot delete yourself
    if user_id == current_admin.id:
        raise BadRequestException("Cannot delete your own account")
    
    # 2. Verify admin password
 
    if not verify_password(request.admin_password, current_admin.hashed_password):
        raise BadRequestException("Admin password is incorrect")
    
    # 3. Get user to delete
    user_to_delete = UserService.get_by_id(db, user_id)
    if not user_to_delete:
        raise NotFoundException("User not found")
    
    # 4. Verify username matches
    if user_to_delete.username.lower() != request.confirm_username.lower():
        raise BadRequestException(
            f"Confirmation username does not match. "
        )
    
    # 5. Perform deletion
    stats = UserService.delete_user_permanently(db, user_id)
    
    return {
        "message": "User permanently deleted",
        "user_id": user_id,
        "chats_deleted": stats["chats_deleted"],
        "messages_deleted": stats["messages_deleted"]
    }