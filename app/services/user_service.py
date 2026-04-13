# app/services/user_service.py
"""
User Service - Async Version with Async Password Hashing

CRITICAL: Uses verify_password_async and get_password_hash_async
to avoid blocking the event loop during login/registration.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, delete
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import secrets
import re
import uuid

from app.middleware.exceptions import BadRequestException, ConflictException, ForbiddenException, NotFoundException
from app.models.user import User, AuthProvider
from app.models.chat import ChatSession
from app.models.message import Message
from app.core.security import (           # Sync - only for non-async contexts
    get_password_hash_async,     # Async - use in endpoints!
    verify_password_async        # Async - use in endpoints!
)
from app.schemas.user import UserCreate


class UserService:
    """Business logic for user operations - Full Async with Async Passwords"""
    
    # ─────────────────────────────────────────────────────────────
    # USERNAME GENERATION
    # ─────────────────────────────────────────────────────────────
    
    @staticmethod
    async def generate_username_from_email(db: AsyncSession, email: str) -> str:
        """Generate a unique username from email."""
        local_part = email.split('@')[0]
        base_username = re.sub(r'[^a-zA-Z0-9]', '_', local_part.lower())
        base_username = re.sub(r'_+', '_', base_username).strip('_')

        if len(base_username) < 3:
            base_username = f"user_{base_username}"

        username = base_username
        existing = await UserService.get_by_username(db, username)
        if not existing:
            return username

        for _ in range(100):
            suffix = secrets.randbelow(10000)
            username = f"{base_username}_{suffix}"
            existing = await UserService.get_by_username(db, username)
            if not existing:
                return username

        return f"{base_username}_{str(uuid.uuid4())[:8]}"

    @staticmethod
    async def generate_username_from_name(db: AsyncSession, full_name: str, email: str) -> str:
        """Generate username from full name, fallback to email."""
        if not full_name or not full_name.strip():
            return await UserService.generate_username_from_email(db, email)

        base_username = re.sub(r'[^a-zA-Z0-9]', '_', full_name.lower())
        base_username = re.sub(r'_+', '_', base_username).strip('_')

        if len(base_username) < 3:
            return await UserService.generate_username_from_email(db, email)

        username = base_username
        existing = await UserService.get_by_username(db, username)
        if not existing:
            return username

        for _ in range(100):
            suffix = secrets.randbelow(10000)
            username = f"{base_username}_{suffix}"
            existing = await UserService.get_by_username(db, username)
            if not existing:
                return username

        return await UserService.generate_username_from_email(db, email)

    # ─────────────────────────────────────────────────────────────
    # READ OPERATIONS
    # ─────────────────────────────────────────────────────────────
    
    @staticmethod
    async def get_by_id(db: AsyncSession, user_id: str) -> Optional[User]:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_email(db: AsyncSession, email: str) -> Optional[User]:
        result = await db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_username(db: AsyncSession, username: str) -> Optional[User]:
        result = await db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_phone(db: AsyncSession, phone_number: str) -> Optional[User]:
        result = await db.execute(select(User).where(User.phone_number == phone_number))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_login(db: AsyncSession, login: str) -> Optional[User]:
        """Get user by email or username."""
        result = await db.execute(
            select(User).where(or_(User.email == login, User.username == login))
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_oauth(db: AsyncSession, provider: AuthProvider, oauth_id: str) -> Optional[User]:
        result = await db.execute(
            select(User).where(User.auth_provider == provider, User.oauth_id == oauth_id)
        )
        return result.scalar_one_or_none()

    # ─────────────────────────────────────────────────────────────
    # CREATE OPERATIONS (using ASYNC password hashing)
    # ─────────────────────────────────────────────────────────────
    
    @staticmethod
    async def create_user(db: AsyncSession, user_data: UserCreate) -> User:
        """Create a new user with password - uses ASYNC hashing."""
        # ASYNC password hashing - doesn't block event loop!
        hashed_password = await get_password_hash_async(user_data.password)

        user = User(
            email=user_data.email,
            username=user_data.username,
            phone_number=user_data.phone_number,
            full_name=user_data.full_name,
            hashed_password=hashed_password,
            auth_provider=AuthProvider.LOCAL,
            is_verified=True,  # OTP verified before registration
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def create_oauth_user(
        db: AsyncSession,
        email: str,
        provider: AuthProvider,
        oauth_id: str,
        full_name: Optional[str] = None,
        avatar_url: Optional[str] = None,
        oauth_username: Optional[str] = None
    ) -> User:
        """Create a new user from OAuth with auto-generated username."""
        if oauth_username:
            username = oauth_username
            existing = await UserService.get_by_username(db, username)
            if existing:
                username = await UserService.generate_username_from_name(db, full_name, email)
        else:
            username = await UserService.generate_username_from_name(db, full_name, email)

        user = User(
            email=email,
            username=username,
            auth_provider=provider,
            oauth_id=oauth_id,
            full_name=full_name,
            avatar_url=avatar_url,
            is_verified=True
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    # ─────────────────────────────────────────────────────────────
    # AUTHENTICATION (using ASYNC password verification)
    # ─────────────────────────────────────────────────────────────
    
    @staticmethod
    async def authenticate(db: AsyncSession, login: str, password: str) -> Optional[User]:
        """
        Authenticate user - uses ASYNC password verification.
        
        This is THE critical fix for login taking 3+ seconds!
        """
        user = await UserService.get_by_login(db, login)
        if not user or not user.hashed_password:
            return None

        # ASYNC password verification - runs in thread pool, doesn't block!
        if not await verify_password_async(password, user.hashed_password):
            return None

        if not user.is_active:
            return None

        user.last_login_at = datetime.now(timezone.utc)
        await db.commit()
        return user

    @staticmethod
    async def update_last_login(db: AsyncSession, user_id: str):
        user = await UserService.get_by_id(db, user_id)
        if user:
            user.last_login_at = datetime.now(timezone.utc)
            await db.commit()

    @staticmethod
    async def reset_password_by_phone(db: AsyncSession, phone_number: str, new_password: str) -> User:
        user = await UserService.get_by_phone(db, phone_number)
        if not user:
            raise NotFoundException("User not found")
        if not user.is_active:
            raise ForbiddenException("User account is inactive")

        user.hashed_password = await get_password_hash_async(new_password)
        await db.commit()
        await db.refresh(user)
        return user
    
    @staticmethod
    async def change_phone(
        db: AsyncSession,
        user_id: str,
        new_phone_number: str,
    ) -> User:
        user = await UserService.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("User not found")

        existing = await UserService.get_by_phone(db, new_phone_number)
        if existing and existing.id != user_id:
            raise ConflictException("Phone number already in use")

        user.phone_number = new_phone_number
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def get_user_stats(db: AsyncSession, user_id: str) -> Dict[str, Any]:
        user = await UserService.get_by_id(db, user_id)
        if not user:
            return {}

        result = await db.execute(
            select(func.count()).select_from(ChatSession).where(ChatSession.user_id == user_id)
        )
        total_chats = result.scalar() or 0

        result = await db.execute(
            select(func.count()).select_from(Message).join(ChatSession).where(ChatSession.user_id == user_id)
        )
        total_messages = result.scalar() or 0

        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        result = await db.execute(
            select(func.count()).select_from(Message).join(ChatSession).where(
                ChatSession.user_id == user_id,
                Message.created_at >= today_start
            )
        )
        messages_today = result.scalar() or 0

        return {
            "total_chats": total_chats,
            "total_messages": total_messages,
            "messages_today": messages_today
        }

    @staticmethod
    async def get_all_users_admin(
        db: AsyncSession,
        skip: int = 0,
        limit: int = 50,
        include_inactive: bool = True,
        search: Optional[str] = None
    ) -> Dict[str, Any]:
        query = select(User)

        if not include_inactive:
            query = query.where(User.is_active == True)

        if search:
            search_filter = or_(
                User.email.ilike(f"%{search}%"),
                User.username.ilike(f"%{search}%"),
                User.full_name.ilike(f"%{search}%"),
                User.phone_number.ilike(f"%{search}%")
            )
            query = query.where(search_filter)

        count_query = select(func.count()).select_from(query.subquery())
        count_result = await db.execute(count_query)
        total = count_result.scalar() or 0

        query = query.order_by(User.created_at.desc()).offset(skip).limit(limit)
        result = await db.execute(query)
        users = result.scalars().all()

        users_with_stats = []
        for user in users:
            stats = await UserService.get_user_stats(db, user.id)
            users_with_stats.append(
                {
                    "id": user.id,
                    "email": user.email,
                    "username": user.username,
                    "phone_number": user.phone_number,
                    "full_name": user.full_name,
                    "auth_provider": user.auth_provider.value,
                    "is_active": user.is_active,
                    "is_admin": user.is_admin,
                    "is_verified": user.is_verified,
                    "created_at": user.created_at,
                    "last_login_at": user.last_login_at,
                    "stats": stats,
                }
            )

        return {"total": total, "users": users_with_stats, "skip": skip, "limit": limit}

    # ─────────────────────────────────────────────────────────────
    # ADMIN OPERATIONS
    # ─────────────────────────────────────────────────────────────
    
    @staticmethod
    async def disable_user(db: AsyncSession, user_id: str) -> User:
        user = await UserService.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("User not found")
        if user.is_admin:
            raise ForbiddenException("Cannot disable admin accounts")
        user.is_active = False
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def enable_user(db: AsyncSession, user_id: str) -> User:
        user = await UserService.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("User not found")
        user.is_active = True
        await db.commit()
        await db.refresh(user)
        return user

    # ─────────────────────────────────────────────────────────────
    # PROFILE OPERATIONS (using ASYNC password functions)
    # ─────────────────────────────────────────────────────────────
    
    @staticmethod
    async def change_password(
        db: AsyncSession,
        user_id: str,
        current_password: str,
        new_password: str
    ) -> User:
        user = await UserService.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("User not found")

        # ASYNC password verification
        if not await verify_password_async(current_password, user.hashed_password):
            raise BadRequestException("Current password is incorrect")

        # ASYNC password hashing
        user.hashed_password = await get_password_hash_async(new_password)
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def change_email(
        db: AsyncSession,
        user_id: str,
        new_email: str,
        password: str
    ) -> User:
        user = await UserService.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("User not found")

        # ASYNC password verification
        if not await verify_password_async(password, user.hashed_password):
            raise BadRequestException("Password is incorrect")

        existing = await UserService.get_by_email(db, new_email)
        if existing and existing.id != user_id:
            raise ConflictException("Email already exists")

        user.email = new_email
        user.is_verified = False
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def update_profile(
        db: AsyncSession,
        user_id: str,
        username: Optional[str] = None,
        full_name: Optional[str] = None,
        avatar_url: Optional[str] = None
    ) -> User:
        user = await UserService.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("User not found")

        if username is not None:
            existing = await UserService.get_by_username(db, username)
            if existing and existing.id != user_id:
                raise ConflictException("Username already exists")
            user.username = username

        if full_name is not None:
            user.full_name = full_name
        if avatar_url is not None:
            user.avatar_url = avatar_url

        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def delete_user_permanently(db: AsyncSession, user_id: str) -> Dict[str, int]:
        user = await UserService.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("User not found")
        if user.is_admin:
            raise ConflictException("Cannot delete admin accounts")

        stats = {"chats_deleted": 0, "messages_deleted": 0}

        result = await db.execute(select(ChatSession).where(ChatSession.user_id == user_id))
        chats = result.scalars().all()

        for chat in chats:
            result = await db.execute(
                select(func.count()).select_from(Message).where(Message.chat_session_id == chat.id)
            )
            msg_count = result.scalar() or 0
            stats["messages_deleted"] += msg_count
            stats["chats_deleted"] += 1
            await db.execute(delete(Message).where(Message.chat_session_id == chat.id))

        await db.execute(delete(ChatSession).where(ChatSession.user_id == user_id))
        await db.delete(user)
        await db.commit()

        return stats