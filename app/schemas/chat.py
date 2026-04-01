from pydantic import BaseModel, Field
from typing import Any, Dict, Optional, List, TypedDict
from datetime import datetime
from langchain_core.documents import Document as LangChainDocument

class ChatCreate(BaseModel):
    title: Optional[str] = "New Chat"


class ChatUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)


class MessageBase(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)

class MessageResponse(BaseModel):
    id: str
    chat_session_id: str
    role: str
    content: str
    usage: Optional[dict] = None
    order_index: int
    metadata: Optional[dict] = None
    created_at: datetime
    class Config:
        from_attributes = True


class ChatResponse(BaseModel):
    id: str
    user_id: str
    title: str
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    last_message_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class ChatWithMessages(ChatResponse):
    messages: List[MessageResponse] = []


class ChatListResponse(BaseModel):
    total: int
    chats: List[ChatResponse]
    skip: int
    limit: int


class RAGQueryResponse(BaseModel):
    """Response from RAG query"""
    message_id: str
    chat_id: str
    user_message: MessageResponse
    assistant_message: MessageResponse
    processing_time_ms: float
    credits_remaining: Optional[int] = None

class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1)


class State(TypedDict, total=False):
    question: str
    conversation_history: List[str]
    enhancement_status: str
    enhanced_query: str
    resolved_query: str
    keywords: List[str]
    rejection_reason: str
    docs: List[LangChainDocument]
    answer: str

    enhancer_usage: Dict[str, Any]
    generator_usage: Dict[str, Any]