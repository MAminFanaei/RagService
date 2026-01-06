from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List
from datetime import datetime, timezone

from app.core.database import get_db
from app.exceptions import NotFoundException
from app.schemas.admin import (
    AdminUserUpdate, UserStatsResponse, SystemStatsResponse,
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


@router.get("/stats/system", response_model=SystemStatsResponse)
async def get_system_stats(
    admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Get overall system statistics"""
    
    # User stats
    total_users = db.query(func.count(User.id)).scalar()
    active_users = db.query(func.count(User.id)).filter(User.is_active == True).scalar()
    
    # Chat stats
    total_chats = db.query(func.count(ChatSession.id)).scalar()
    
    # Message stats
    total_messages = db.query(func.count(Message.id)).scalar()
    
    # Messages today
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    messages_today = db.query(func.count(Message.id)).filter(
        Message.created_at >= today_start
    ).scalar()
    
    # RAG stats
    rag_stats = RAGService.get_rag_stats()
    
    # Average response time (last 100 messages with metadata)
    recent_messages = db.query(Message).filter(
        Message.meta_data.isnot(None)
    ).order_by(Message.created_at.desc()).limit(100).all()
    
    avg_response_time = 0
    if recent_messages:
        times = [
            msg.meta_data.get("processing_time_ms", 0)
            for msg in recent_messages
            if msg.meta_data and "processing_time_ms" in msg.meta_data
        ]
        avg_response_time = sum(times) / len(times) if times else 0
    
    return {
        "total_users": total_users or 0,
        "active_users": active_users or 0,
        "total_chats": total_chats or 0,
        "total_messages": total_messages or 0,
        "messages_today": messages_today or 0,
        "rag_engine_status": rag_stats.get("status", "unknown"),
        "documents_indexed": rag_stats.get("documents_count", 0),
        "average_response_time_ms": avg_response_time
    }


@router.get("/stats/rag", response_model=RAGStatsResponse)
async def get_rag_stats(
    admin: User = Depends(get_current_admin_user)
):
    """Get RAG engine statistics"""
    stats = RAGService.get_rag_stats()
    return stats


@router.get("/users", response_model=List[UserStatsResponse])
async def list_all_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: str = Query(None),
    admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """List all users with statistics"""
    result = UserService.list_users(db, skip=skip, limit=limit, search=search)
    
    users_with_stats = []
    for user in result["users"]:
        stats = UserService.get_user_stats(db, user.id)
        
        users_with_stats.append({
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
            "max_messages_per_day": user.max_messages_per_day ,
            "rate_limit_per_minute": user.rate_limit_per_minute
        })

    return users_with_stats


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
    if updates.is_active is not None:
        user.is_active = updates.is_active
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