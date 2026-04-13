# app/api/v1/admin.py
"""
Admin Endpoints - Async Version
"""
from fastapi import APIRouter, Depends, Query , Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List, Optional
from datetime import datetime, timezone, timedelta
import structlog
from app.core.database import get_db
from app.core.security import get_password_hash_async, verify_password
from app.middleware.exceptions import BadRequestException, NotFoundException
from app.schemas.admin import (
    AdminUserUpdate, UserActionResponse, UserDeleteRequest, UserDeleteResponse,
    UserDisableRequest, UserStatsResponse, ConversationExport , AdminPasswordResetResponse , AdminPasswordResetRequest ,     AdminCreditAdjustRequest, AdminCreditAdjustResponse,AdminWalletTopUpRequest, AdminWalletTopUpResponse,
)
from app.services.credit_service import CreditService   
from app.core.database import check_db_health, check_redis_health
from app.services.user_service import UserService
from app.services.chat_service import ChatService
from app.api.deps import get_current_admin_user
from app.models.user import User
from app.models.chat import ChatSession
from app.models.message import Message
from app.config import settings

router = APIRouter(prefix="/admin", tags=["Admin"])

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
        structlog.dev.ConsoleRenderer()
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


@router.get("/users")
async def list_all_users(
    skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
    include_inactive: bool = Query(True), search: Optional[str] = Query(None),
    current_admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db)
):
    return await UserService.get_all_users_admin(db=db, skip=skip, limit=limit, include_inactive=include_inactive, search=search)


@router.get("/users/{user_id}", response_model=UserStatsResponse)
async def get_user_details(user_id: str, admin: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    user = await UserService.get_by_id(db, user_id)
    if not user:
        raise NotFoundException("User not found")
    stats = await UserService.get_user_stats(db, user_id)
    return {
        "id": user.id, "email": user.email, "username": user.username,
        "auth_provider": user.auth_provider.value, "is_active": user.is_active,
        "is_admin": user.is_admin, "created_at": user.created_at,
        "last_login_at": user.last_login_at, "total_chats": stats.get("total_chats", 0),
        "total_messages": stats.get("total_messages", 0), "messages_today": stats.get("messages_today", 0),
        "max_messages_per_day": user.max_messages_per_day, "rate_limit_per_minute": user.rate_limit_per_minute
    }


@router.patch("/users/{user_id}", response_model=UserStatsResponse)
async def update_user_settings(user_id: str, updates: AdminUserUpdate, admin: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    user = await UserService.get_by_id(db, user_id)
    if not user:
        raise NotFoundException("User not found")
    
    if updates.is_admin is not None:
        user.is_admin = updates.is_admin
    if updates.max_messages_per_day is not None:
        user.max_messages_per_day = updates.max_messages_per_day
    if updates.rate_limit_per_minute is not None:
        user.rate_limit_per_minute = updates.rate_limit_per_minute
    
    await db.commit()
    await db.refresh(user)
    stats = await UserService.get_user_stats(db, user_id)
    
    return {
        "id": user.id, "email": user.email, "username": user.username,
        "auth_provider": user.auth_provider.value, "is_active": user.is_active,
        "is_admin": user.is_admin, "created_at": user.created_at,
        "last_login_at": user.last_login_at, "total_chats": stats.get("total_chats", 0),
        "total_messages": stats.get("total_messages", 0), "messages_today": stats.get("messages_today", 0),
        "max_messages_per_day": user.max_messages_per_day, "rate_limit_per_minute": user.rate_limit_per_minute
    }


@router.get("/users/{user_id}/conversations", response_model=List[ConversationExport])
async def export_user_conversations(user_id: str, admin: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    user = await UserService.get_by_id(db, user_id)
    if not user:
        raise NotFoundException("User not found")
    
    result = await db.execute(select(ChatSession).where(ChatSession.user_id == user_id).order_by(ChatSession.created_at.desc()))
    chats = result.scalars().all()
    
    conversations = []
    for chat in chats:
        conv = await ChatService.export_conversation(db, chat.id)
        if conv:
            conversations.append(conv)
    return conversations


@router.get("/stats/user_usage")
async def get_system_stats(current_admin: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(func.count()).select_from(User))
    total_users = result.scalar()
    
    result = await db.execute(select(func.count()).select_from(User).where(User.is_active == True))
    active_users = result.scalar()
    
    result = await db.execute(select(func.count()).select_from(ChatSession))
    total_chats = result.scalar()
    
    result = await db.execute(select(func.count()).select_from(Message))
    total_messages = result.scalar()
    
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(select(func.count()).select_from(Message).where(Message.created_at >= today_start))
    messages_today = result.scalar()
    
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    result = await db.execute(select(func.count()).select_from(User).where(User.last_login_at >= week_ago))
    active_last_week = result.scalar()
    
    return {
        "users": {"total": total_users, "active": active_users, "inactive": total_users - active_users, "active_last_7_days": active_last_week},
        "chats": {"total": total_chats},
        "messages": {"total": total_messages, "today": messages_today}
    }

@router.get("/stats/service_detail")
async def health_check(
    request: Request,
    current_admin: User = Depends(get_current_admin_user)
):
    """Health check endpoint."""

    db_health = await check_db_health()
    redis_health = await check_redis_health()
    rag_stats = request.app.state.rag_engine.get_stats()  # ← this is the key

    return {
        "status": "healthy" if db_health["status"] == "healthy" and redis_health["status"] == "healthy" else "degraded",
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "database": db_health,
        "redis": redis_health,
        "rag_engine": rag_stats
    }

@router.post("/users/{user_id}/disable", response_model=UserActionResponse)
async def disable_user(user_id: str, request: UserDisableRequest = None, current_admin: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    if user_id == current_admin.id:
        raise BadRequestException("Cannot disable your own account")
    user = await UserService.disable_user(db, user_id)
    return {"message": "User disabled successfully", "user_id": user_id, "is_active": False}


@router.post("/users/{user_id}/enable", response_model=UserActionResponse)
async def enable_user(user_id: str, current_admin: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    user = await UserService.enable_user(db, user_id)
    return {"message": "User enabled successfully", "user_id": user_id, "is_active": True}


@router.delete("/users/{user_id}", response_model=UserDeleteResponse)
async def delete_user_permanently(user_id: str, request: UserDeleteRequest, current_admin: User = Depends(get_current_admin_user), db: AsyncSession = Depends(get_db)):
    if user_id == current_admin.id:
        raise BadRequestException("Cannot delete your own account")
    if not verify_password(request.admin_password, current_admin.hashed_password):
        raise BadRequestException("Admin password is incorrect")
    
    user_to_delete = await UserService.get_by_id(db, user_id)
    if not user_to_delete:
        raise NotFoundException("User not found")
    
    stats = await UserService.delete_user_permanently(db, user_id)
    
    return {"message": "User permanently deleted", "user_id": user_id, "chats_deleted": stats["chats_deleted"], "messages_deleted": stats["messages_deleted"]}


@router.put("/users/{user_id}/reset_password", response_model=AdminPasswordResetResponse)
async def admin_reset_user_password(
    user_id: str,
    request: AdminPasswordResetRequest,
    current_admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db)
):
    """
    [ADMIN ONLY] Reset a user's password.

    """
    # 1. Cannot reset your own password through this endpoint
    if user_id == current_admin.id:
        raise BadRequestException(
            "Cannot reset your own password through this endpoint. Use /auth/me/reset-password instead."
        )
    
    # 2. Verify admin password
    if not verify_password(request.admin_password, current_admin.hashed_password):
        raise BadRequestException("Admin password is incorrect")
    
    # 3. Get target user
    target_user = await UserService.get_by_id(db, user_id)
    if not target_user:
        raise NotFoundException("User not found")
    
    # 5. Hash and set new password
    target_user.hashed_password = await get_password_hash_async(request.new_password)
    await db.commit()
    await db.refresh(target_user)
    
    # 6. Log the action
    logger.info(
        "Admin reset user password",
        admin_id=current_admin.id,
        admin_email=current_admin.email or "no email available",
        target_user_id=target_user.id,
        target_user_email=target_user.email or "no email available",
        target_user_username=target_user.username  or "no username available"
    )
    
    return AdminPasswordResetResponse(
        message="Password reset successfully",
        username=target_user.username or target_user.id,
        email=target_user.email or ""
    )

@router.post(
    "/users/{user_id}/credits",
    response_model=AdminCreditAdjustResponse,
)
async def admin_add_user_credits(
    user_id: str,
    request: AdminCreditAdjustRequest,
    current_admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """
    [ADMIN ONLY] Add message credits to a user account.
    Does NOT charge the wallet — this is a free grant.
    """
    target_user = await UserService.get_by_id(db, user_id)
    if not target_user:
        raise NotFoundException("User not found")
    
    if not verify_password(request.admin_password, current_admin.hashed_password):
        raise BadRequestException("Admin password is incorrect")

    result = await CreditService.admin_add_credits(
        db=db,
        user_id=user_id,
        amount=request.amount,
        reason=request.reason,
        admin_id=current_admin.id,
    )
    await db.commit()

    logger.info(
        "admin_added_credits",
        admin_id=current_admin.id,
        admin_email=current_admin.email or "no email",
        target_user_id=user_id,
        amount=request.amount,
        reason=request.reason,
    )

    return AdminCreditAdjustResponse(
        message=f"Successfully added {request.amount} credits to user account.",
        user_id=user_id,
        credits_added=result["credits_added"],
        new_remaining=result["new_remaining"],
        total_purchased=result["total_purchased"],
    )


@router.post(
    "/users/{user_id}/wallet/topup",
    response_model=AdminWalletTopUpResponse,
)
async def admin_topup_user_wallet(
    user_id: str,
    request: AdminWalletTopUpRequest,
    current_admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """
    [ADMIN ONLY] Add money (Rials) to a user wallet.
    Creates the wallet automatically if the user has none.
    """
    target_user = await UserService.get_by_id(db, user_id)
    if not target_user:
        raise NotFoundException("User not found")
    
    if not verify_password(request.admin_password, current_admin.hashed_password):
        raise BadRequestException("Admin password is incorrect")
    result = await CreditService.admin_add_wallet_balance(
        db=db,
        user_id=user_id,
        amount=request.amount,
        reason=request.reason,
        admin_id=current_admin.id,
    )
    await db.commit()

    logger.info(
        "admin_topped_up_wallet",
        admin_id=current_admin.id,
        admin_email=current_admin.email or "no email",
        target_user_id=user_id,
        amount=request.amount,
        reason=request.reason,
    )

    return AdminWalletTopUpResponse(
        message=f"Successfully added {request.amount:,} Rials to user wallet.",
        user_id=user_id,
        amount_added=result["amount_added"],
        new_balance=result["new_balance"],
        wallet_id=result["wallet_id"],
    )