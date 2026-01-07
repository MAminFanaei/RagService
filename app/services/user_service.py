from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, timezone

from app.exceptions import BadRequestException, ConflictException, ForbiddenException, NotFoundException
from app.models.user import User, AuthProvider
from app.models.chat import ChatSession
from app.models.message import Message
from app.core.security import get_password_hash, verify_password
from app.schemas.user import UserCreate
import secrets , re , uuid 

class UserService:
    """Business logic for user operations"""
    @staticmethod
    def generate_username_from_email(db: Session, email: str) -> str:
        """
        Generate a unique username from email.
        
        Example: "john.doe@gmail.com" -> "john_doe" or "john_doe_1234"
        """
        # Extract local part of email
        local_part = email.split('@')[0]
        
        # Clean: replace dots/special chars with underscore
        base_username = re.sub(r'[^a-zA-Z0-9]', '_', local_part.lower())
        
        # Remove consecutive underscores
        base_username = re.sub(r'_+', '_', base_username)
        
        # Trim underscores from ends
        base_username = base_username.strip('_')
        
        # Ensure minimum length
        if len(base_username) < 3:
            base_username = f"user_{base_username}"
        
        # Check if available
        username = base_username
        if not UserService.get_by_username(db, username):
            return username
        
        # Add random suffix until unique
        for _ in range(100):  # Max attempts
            suffix = secrets.randbelow(10000)
            username = f"{base_username}_{suffix}"
            if not UserService.get_by_username(db, username):
                return username
        
        # Fallback: use UUID fragment

        return f"{base_username}_{str(uuid.uuid4())[:8]}"
    
    @staticmethod
    def generate_username_from_name(db: Session, full_name: str, email: str) -> str:
        """
        Generate username from full name, fallback to email.
        
        Example: "John Doe" -> "john_doe" or "john_doe_1234"
        """
        if not full_name or not full_name.strip():
            return UserService.generate_username_from_email(db, email)
        
        # Clean name: lowercase, replace spaces with underscore
        base_username = re.sub(r'[^a-zA-Z0-9]', '_', full_name.lower())
        base_username = re.sub(r'_+', '_', base_username).strip('_')
        
        if len(base_username) < 3:
            return UserService.generate_username_from_email(db, email)
        
        # Check if available
        username = base_username
        if not UserService.get_by_username(db, username):
            return username
        
        # Add random suffix
        for _ in range(100):
            suffix = secrets.randbelow(10000)
            username = f"{base_username}_{suffix}"
            if not UserService.get_by_username(db, username):
                return username
        
        return UserService.generate_username_from_email(db, email)
    # ─────────────────────────────────────────────────────────────
    # READ OPERATIONS
    # ─────────────────────────────────────────────────────────────
    
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
        
    # ─────────────────────────────────────────────────────────────
    # CREATE OPERATIONS
    # ─────────────────────────────────────────────────────────────
    
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
    
    # ─────────────────────────────────────────────────────────────
    # AUTHENTICATION
    # ─────────────────────────────────────────────────────────────
    
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
    
    # ─────────────────────────────────────────────────────────────
    # STATS & QUERIES
    # ─────────────────────────────────────────────────────────────
    
    @staticmethod
    def get_user_stats(db: Session, user_id: str) -> Dict[str, Any]:
        """Get user usage statistics"""
        user = UserService.get_by_id(db, user_id)
        if not user:
            return {}
        
        total_chats = db.query(func.count(ChatSession.id)).filter(
            ChatSession.user_id == user_id
        ).scalar()
        
        total_messages = db.query(func.count(Message.id)).join(
            ChatSession
        ).filter(
            ChatSession.user_id == user_id
        ).scalar()
        
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
            Message.role == "user"
        ).scalar()
        
        return (messages_today or 0) < max_messages
    
    @staticmethod
    def get_all_users_admin(
        db: Session,
        skip: int = 0,
        limit: int = 50,
        include_inactive: bool = True,
        search: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get all users for admin panel."""
        query = db.query(User)
        
        if not include_inactive:
            query = query.filter(User.is_active == True)
        
        if search:
            search_filter = or_(
                User.email.ilike(f"%{search}%"),
                User.username.ilike(f"%{search}%"),
                User.full_name.ilike(f"%{search}%")
            )
            query = query.filter(search_filter)
        
        total = query.count()
        users = query.order_by(User.created_at.desc()).offset(skip).limit(limit).all()
        
        users_with_stats = []
        for user in users:
            user_dict = {
                "id": user.id,
                "email": user.email,
                "username": user.username,
                "full_name": user.full_name,
                "auth_provider": user.auth_provider.value,
                "is_active": user.is_active,
                "is_admin": user.is_admin,
                "is_verified": user.is_verified,
                "created_at": user.created_at,
                "last_login_at": user.last_login_at,
                "stats": UserService.get_user_stats(db, user.id)
            }
            users_with_stats.append(user_dict)
        
        return {
            "total": total,
            "users": users_with_stats,
            "skip": skip,
            "limit": limit
        }
    
    # ─────────────────────────────────────────────────────────────
    # ADMIN OPERATIONS
    # ─────────────────────────────────────────────────────────────
    
    @staticmethod
    def disable_user(db: Session, user_id: str) -> User:
        """
        Disable a user account.
        
        Raises:
            UserNotFoundException: User not found
            AdminProtectedException: Cannot disable admin
        """
        user = UserService.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("User not found")
        
        if user.is_admin:
            raise ForbiddenException("This operation is not allowed for admin accounts")
        
        user.is_active = False
        db.commit()
        db.refresh(user)
        
        return user
    
    @staticmethod
    def enable_user(db: Session, user_id: str) -> User:
        """
        Re-enable a disabled user account.
        
        Raises:
            UserNotFoundException: User not found
        """
        user = UserService.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("User not found")
        
        user.is_active = True
        db.commit()
        db.refresh(user)
        
        return user
    

    # ─────────────────────────────────────────────────────────────
    # PROFILE UPDATE OPERATIONS
    # ─────────────────────────────────────────────────────────────
    
    @staticmethod
    def change_password(
        db: Session,
        user_id: str,
        current_password: str,
        new_password: str
    ) -> User:
        """
        Change user's password after verifying current password.
        
        Raises:
            UserNotFoundException: User not found
            OAuthUserException: User is OAuth-only
            InvalidPasswordException: Current password wrong
        """
        user = UserService.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("User not found")
        
        if not verify_password(current_password, user.hashed_password):
            raise BadRequestException("Current password is incorrect")
        
        user.hashed_password = get_password_hash(new_password)
        db.commit()
        db.refresh(user)
        
        return user
    
    @staticmethod
    def change_email(
        db: Session,
        user_id: str,
        new_email: str,
        password: str
    ) -> User:
        """
        Change user's email after password verification.
        
        Raises:
            UserNotFoundException: User not found
            OAuthUserException: User is OAuth-only
            InvalidPasswordException: Password wrong
            EmailAlreadyExistsException: Email taken
        """
        user = UserService.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("User not found")
        
        if not verify_password(password, user.hashed_password):
            raise BadRequestException("Password is incorrect")
        
        existing = UserService.get_by_email(db, new_email)
        if existing and existing.id != user_id:
            raise ConflictException("Email Already Exist!")
        
        user.email = new_email
        user.is_verified = False  # Require re-verification
        db.commit()
        db.refresh(user)
        
        return user
    
    @staticmethod
    def update_profile(
        db: Session,
        user_id: str,
        username: Optional[str] = None,
        full_name: Optional[str] = None,
        avatar_url: Optional[str] = None
    ) -> User:
        """
        Update user profile fields.
        
        Raises:
            UserNotFoundException: User not found
            UsernameAlreadyExistsException: Username taken
        """
        user = UserService.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("User not found")
        
        if username is not None:
            existing = UserService.get_by_username(db, username)
            if existing and existing.id != user_id:
                raise ConflictException("Username Already Exist!")
            user.username = username
        
        if full_name is not None:
            user.full_name = full_name
        
        if avatar_url is not None:
            user.avatar_url = avatar_url
        
        db.commit()
        db.refresh(user)
        
        return user
        
    @staticmethod
    def delete_user_permanently(db: Session, user_id: str) -> Dict[str, int]:
        """
        Permanently delete a user and all their data.
        
        Raises:
            UserNotFoundException: User not found
            AdminProtectedException: Cannot delete admin
            
        Returns:
            Dict with counts of deleted items
        """
        
        user = UserService.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("User Not Found")
        
        if user.is_admin:
            raise ConflictException("Cannot delete admins")
        
        stats = {
            "chats_deleted": 0,
            "messages_deleted": 0
        }
        
        # Get all user's chats
        chats = db.query(ChatSession).filter(
            ChatSession.user_id == user_id
        ).all()
        
        # Delete messages for each chat
        for chat in chats:
            msg_count = db.query(Message).filter(
                Message.chat_session_id == chat.id
            ).delete()
            stats["messages_deleted"] += msg_count
            stats["chats_deleted"] += 1
        
        # Delete all chats
        db.query(ChatSession).filter(
            ChatSession.user_id == user_id
        ).delete()
        
        # Delete user
        db.delete(user)
        db.commit()
        
        return stats