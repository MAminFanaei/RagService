from pydantic import BaseModel, Field , model_validator , field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone


class AdminUserUpdate(BaseModel):
    """Admin can update user settings"""
    is_admin: Optional[bool] = None
    max_messages_per_day: Optional[int] = Field(None, ge=0, le=10000)
    rate_limit_per_minute: Optional[int] = Field(None, ge=1, le=1000)


class UserStatsResponse(BaseModel):
    """Detailed user statistics for admin"""
    id: str
    email: str
    username: Optional[str]
    auth_provider: str
    is_active: bool
    is_admin: bool
    created_at: datetime
    last_login_at: Optional[datetime]
    
    # Usage stats
    total_chats: int
    total_messages: int
    messages_today: int
    
    # Limits
    max_messages_per_day: Optional[int]
    rate_limit_per_minute: Optional[int]


class SystemStatsResponse(BaseModel):
    """Overall system statistics"""
    total_users: int
    active_users: int
    total_chats: int
    total_messages: int
    messages_today: int
    
    # RAG stats
    rag_engine_status: str
    documents_indexed: int
    average_response_time_ms: float


class RAGConfigUpdate(BaseModel):
    """Update RAG configuration (requires restart)"""
    embedding_model: Optional[str] = None
    chunk_tokens: Optional[int] = Field(None, ge=100, le=2000)
    chunk_overlap: Optional[int] = Field(None, ge=0, le=500)
    enhancer_max_token: Optional[int] = Field(None, ge=100, le=2000)
    answer_llm_max_token: Optional[int] = Field(None, ge=500, le=5000)


class RAGStatsResponse(BaseModel):
    """RAG engine statistics"""
    model: str
    index: str
    documents_count: int
    device: str
    status: str


class ConversationExport(BaseModel):
    """Export user conversations"""
    user_id: str
    chat_id: str
    title: str
    messages: List[Dict[str, Any]]
    created_at: datetime
    message_count: int

class UserDisableRequest(BaseModel):
    reason: Optional[str] = None

class UserDeleteRequest(BaseModel):
    """Request body for permanent user deletion"""
    admin_password: str 
    confirm_username: Optional[str]

class UserDeleteResponse(BaseModel):
    message: str
    user_id: str
    chats_deleted: int
    messages_deleted: int


class UserActionResponse(BaseModel):
    message: str
    user_id: str
    is_active: bool


class AdminPasswordResetRequest(BaseModel):
    """Request body for admin to reset a user's password."""
    admin_password: str = Field(..., min_length=1, description="Admin's own password for verification")
    new_password: str = Field(..., min_length=8, description="New password for the target user")
    confirm_new_password: str = Field(..., min_length=8, description="Confirm the new password")
    
    @field_validator('new_password')
    @classmethod
    def password_strength(cls, v: str) -> str:
        """Validate password has minimum requirements."""
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters')
        if v.isdigit():
            raise ValueError('Password cannot be all numbers')
        if v.isalpha():
            raise ValueError('Password must contain at least one number')
        return v
    
    @model_validator(mode='after')
    def passwords_match(self) -> 'AdminPasswordResetRequest':
        """Validate that new_password and confirm_new_password match."""
        if self.new_password != self.confirm_new_password:
            raise ValueError('Passwords do not match')
        return self


class AdminPasswordResetResponse(BaseModel):
    """Response for admin password reset."""
    message: str
    user_id: str
    email: str