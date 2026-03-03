from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from typing import Optional
from datetime import datetime, timedelta, timezone
from app.middleware.exceptions import BadRequestException
from app.models.user import AuthProvider
import re


class UserBase(BaseModel):
    email: Optional[EmailStr] = None
    username: Optional[str] = Field(None, min_length=3, max_length=50)
    full_name: Optional[str] = None

    @field_validator('email', mode='before')
    @classmethod
    def validate_email(cls, v):
        if not v:
            return None
        if not v.isascii():
            raise BadRequestException("email must contain only allowed characters (a-z , A-Z, 0-9, and special characters)")
        return v

    @field_validator('username', mode='before')
    @classmethod
    def validate_username(cls, v):
        if not v:
            return None
        if not re.match(r'^[a-zA-Z0-9_-]+$', v):
            raise BadRequestException('Username can only contain English letters, numbers, underscores, and hyphens')
        return v

    @model_validator(mode='after')
    def check_email_or_username(self):
        if not self.email and not self.username:
            raise BadRequestException('Either email or username must be provided')
        return self


class UserCreate(UserBase):
    password: str = Field(..., min_length=6,max_length=128)
    
    @field_validator('password')
    @classmethod
    def password_strength(cls, v):
        if not v.isascii():
            raise BadRequestException("Password must contain only allowed characters (a-z , A-Z, 0-9, and special characters)")
        if len(v) < 6:
            raise BadRequestException('Password must be at least 6 characters')
        if not re.search(r'[A-Z]', v):
            raise BadRequestException('Password must contain at least one uppercase letter')
        if not re.search(r'[0-9]', v):
            raise BadRequestException('Password must contain at least one digit')
        return v


class UserLogin(BaseModel):
    login: str  # Can be email or username
    password: str

class UserResponse(UserBase):
    id: Optional[str] = None
    auth_provider: Optional[AuthProvider] = None
    email: Optional[EmailStr] = None      # was EmailStr (required)
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

# ─────────────────────────────────────────────────────────────
# Profile Update Schemas
# ─────────────────────────────────────────────────────────────

class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=6,max_length=128)
    
    @field_validator('new_password')
    @classmethod
    def password_strength(cls, v):
        if not v.isascii():
            raise BadRequestException('Password must contain only allowed characters (a-z , A-Z, 0-9, and special characters)')
        if len(v) < 6:
            raise BadRequestException('Password must be at least 8 characters')
        if not re.search(r'[A-Z]', v):
            raise BadRequestException('Password must contain at least one uppercase letter')
        if not re.search(r'[0-9]', v):
            raise BadRequestException('Password must contain at least one digit')
        return v


class ProfileUpdateRequest(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=50)
    full_name: Optional[str] = Field(None, max_length=100)
    avatar_url: Optional[str] = None

    @field_validator('username', mode='before')
    @classmethod
    def validate_username(cls, v):
        if not v:
            return None
        if not re.match(r'^[a-zA-Z0-9_-]+$', v):
            raise ValueError('Username can only contain English letters, numbers, underscores, and hyphens')
        return v

# ─────────────────────────────────────────────────────────────
# Profile Update Schemas
# ─────────────────────────────────────────────────────────────
class SuccessResponse(BaseModel):
    """Simple success message response"""
    message: str

class PasswordChangeResponse(SuccessResponse):
    """Response for password change"""
    pass # same as "SuccessResponse"
class EmailChangeRequest(BaseModel):
    new_email: EmailStr
    password: str

    @field_validator('new_email', mode='before')
    @classmethod
    def validate_email(cls, v):
        if not v.isascii():
            raise BadRequestException("Email must contain only allowed characters (a-z , A-Z, 0-9, and special characters)")
        return v
class EmailChangeResponse(BaseModel):
    message: str
    new_email: str
    is_verified: bool


class ProfileUpdateResponse(BaseModel):
    message: str
    user: UserResponse

class AccountDeleteRequest(BaseModel):
    """Request body for self-deletion"""
    password: str
    confirm_email: EmailStr


