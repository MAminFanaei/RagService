from pydantic import BaseModel, EmailStr, Field, validator
from typing import Optional
from datetime import datetime, timedelta, timezone
from app.models.user import AuthProvider


class UserBase(BaseModel):
    email: EmailStr
    username: Optional[str] = None
    full_name: Optional[str] = None


class UserCreate(UserBase):
    password: str = Field(..., min_length=8)
    
    @validator('password')
    def password_strength(cls, v):
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters')
        if not any(c.isupper() for c in v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not any(c.islower() for c in v):
            raise ValueError('Password must contain at least one lowercase letter')
        if not any(c.isdigit() for c in v):
            raise ValueError('Password must contain at least one digit')
        return v


class UserLogin(BaseModel):
    login: str  # Can be email or username
    password: str


class UserUpdate(BaseModel):
    username: Optional[str] = None
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None


class UserResponse(UserBase):
    id: Optional[str] = None
    auth_provider: Optional[AuthProvider] = None
    email: Optional[str] = None
    username: Optional[str] = None
    is_active: Optional[bool] = None
    is_admin: bool  # Required - no default
    is_verified: Optional[bool] = None
    avatar_url: Optional[str] = None
    created_at: datetime  # Required - no default
    last_login_at: Optional[datetime] = None
    
    # Rate limiting info
    max_messages_per_day: Optional[int] = None
    rate_limit_per_minute: Optional[int] = None
    remaining_messages_today: Optional[int] = None
    
    class Config:
        from_attributes = True


class UserWithStats(UserResponse):
    """Extended user info with usage statistics"""
    total_chats: int = 0
    total_messages: int = 0
    messages_today: int = 0