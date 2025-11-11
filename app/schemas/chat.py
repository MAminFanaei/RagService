from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class ChatCreate(BaseModel):
    title: Optional[str] = "New Chat"


class ChatUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)


class MessageBase(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)


class MessageCreate(MessageBase):
    pass


class MessageResponse(BaseModel):
    id: str
    chat_session_id: str
    role: str
    content: str
    usage: dict
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