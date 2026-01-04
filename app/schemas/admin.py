from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone


class AdminUserUpdate(BaseModel):
    """Admin can update user settings"""
    is_active: Optional[bool] = None
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