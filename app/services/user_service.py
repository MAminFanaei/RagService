from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, timezone

from app.models.user import User, AuthProvider
from app.models.chat import ChatSession
from app.models.message import Message
from app.core.security import get_password_hash, verify_password
from app.schemas.user import UserCreate


class UserService:
    """Business logic for user operations"""
    
    @staticmethod
    def get_by_id(db: Session, user_id: str) -> Optional[User]:
        return db.query(User).filter(User.id == user_id).first()
    
    @staticmethod
    def get_by_email(db: Session, email: str) -> Optional[User]:
        return db.query(User).filter(User.email == email).first()
    
    @staticmethod
    def get_by_username(db: Session, username: str) -> Optional[User]:
        return db.query(User).filter(User.username == username).first()
    
    @staticmethod
    def get_by_login(db: Session, login: str) -> Optional[User]:
        """Get user by email or username"""
        return db.query(User).filter(
            or_(User.email == login, User.username == login)
        ).first()
    
    @staticmethod
    def get_by_oauth(db: Session, provider: AuthProvider, oauth_id: str) -> Optional[User]:
        return db.query(User).filter(
            User.auth_provider == provider,
            User.oauth_id == oauth_id
        ).first()
    
    @staticmethod
    def create_user(db: Session, user_data: UserCreate) -> User:
        """Create a new user with password"""
        user = User(
            email=user_data.email,
            username=user_data.username,
            full_name=user_data.full_name,
            hashed_password=get_password_hash(user_data.password),
            auth_provider=AuthProvider.LOCAL,
            is_verified=False  # Require email verification in production
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    
    @staticmethod
    def create_oauth_user(
        db: Session,
        email: str,
        provider: AuthProvider,
        oauth_id: str,
        full_name: Optional[str] = None,
        avatar_url: Optional[str] = None
    ) -> User:
        """Create a new user from OAuth"""
        user = User(
            email=email,
            auth_provider=provider,
            oauth_id=oauth_id,
            full_name=full_name,
            avatar_url=avatar_url,
            is_verified=True  # OAuth users are pre-verified
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    
    @staticmethod
    def authenticate(db: Session, login: str, password: str) -> Optional[User]:
        """Authenticate user with email/username and password"""
        user = UserService.get_by_login(db, login)
        if not user or not user.hashed_password:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        if not user.is_active:
            return None
        
        # Update last login
        user.last_login_at = datetime.now(timezone.utc)
        db.commit()
        
        return user
    
    @staticmethod
    def update_last_login(db: Session, user_id: str):
        """Update user's last login timestamp"""
        user = UserService.get_by_id(db, user_id)
        if user:
            user.last_login_at = datetime.now(timezone.utc)
            db.commit()
    
    @staticmethod
    def get_user_stats(db: Session, user_id: str) -> Dict[str, Any]:
        """Get user usage statistics"""
        user = UserService.get_by_id(db, user_id)
        if not user:
            return {}
        
        # Total chats
        total_chats = db.query(func.count(ChatSession.id)).filter(
            ChatSession.user_id == user_id
        ).scalar()
        
        # Total messages
        total_messages = db.query(func.count(Message.id)).join(
            ChatSession
        ).filter(
            ChatSession.user_id == user_id
        ).scalar()
        
        # Messages today
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        messages_today = db.query(func.count(Message.id)).join(
            ChatSession
        ).filter(
            ChatSession.user_id == user_id,
            Message.created_at >= today_start
        ).scalar()
        
        return {
            "total_chats": total_chats or 0,
            "total_messages": total_messages or 0,
            "messages_today": messages_today or 0
        }
    
    @staticmethod
    def check_message_quota(db: Session, user_id: str, max_messages: int) -> bool:
        """Check if user has exceeded daily message quota"""
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        
        messages_today = db.query(func.count(Message.id)).join(
            ChatSession
        ).filter(
            ChatSession.user_id == user_id,
            Message.created_at >= today_start,
            Message.role == "user"  # Only count user messages
        ).scalar()
        
        return (messages_today or 0) < max_messages
    
    @staticmethod
    def list_users(db: Session, skip: int = 0, limit: int = 100, search: Optional[str] = None):
        """List all users with optional search"""
        query = db.query(User)
        
        if search:
            search_filter = or_(
                User.email.contains(search),
                User.username.contains(search),
                User.full_name.contains(search)
            )
            query = query.filter(search_filter)
        
        total = query.count()
        users = query.offset(skip).limit(limit).all()
        
        return {"total": total, "users": users, "skip": skip, "limit": limit}