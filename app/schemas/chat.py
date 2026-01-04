from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime, timedelta, timezone
import json

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
    usage: Optional[dict] = None
    order_index: int
    metadata: Optional[dict] = None
    created_at: datetime
    
    # @field_validator('metadata', mode='before')
    # @classmethod
    # def convert_metadata_to_dict(cls, v):
    #     """Convert LangChain MetaData objects to dict, filtering out non-serializable objects"""
    #     if v is None:
    #         return None
        
    #     # If it's already a dict, sanitize it
    #     if isinstance(v, dict):
    #         return cls._sanitize_dict(v)
        
    #     # If it's a LangChain object with dict() method
    #     if hasattr(v, 'dict'):
    #         return cls._sanitize_dict(v.dict())
        
    #     # If it has __dict__ attribute
    #     if hasattr(v, '__dict__'):
    #         return cls._sanitize_dict(v.__dict__)
        
    #     # Last resort
    #     try:
    #         return cls._sanitize_dict(dict(v))
    #     except:
    #         return {}

    # @staticmethod
    # def _sanitize_dict(d: dict) -> dict:
    #     """Remove non-serializable objects from dict"""
    #     clean = {}
    #     for key, value in d.items():
    #         try:
    #             # Skip SQLAlchemy objects
    #             if hasattr(value, '__tablename__') or hasattr(value, 'metadata'):
    #                 continue
                
    #             # Skip classes/modules
    #             if isinstance(value, type) or str(type(value)).startswith("<class 'sqlalchemy"):
    #                 continue
                
    #             # Recursively clean nested dicts
    #             if isinstance(value, dict):
    #                 clean[key] = MessageResponse._sanitize_dict(value)
    #             # Clean lists
    #             elif isinstance(value, (list, tuple)):
    #                 clean[key] = [
    #                     MessageResponse._sanitize_dict(item) if isinstance(item, dict) else item
    #                     for item in value
    #                     if not hasattr(item, '__tablename__')
    #                 ]
    #             # Keep simple types
    #             elif isinstance(value, (str, int, float, bool, type(None))):
    #                 clean[key] = value
    #             # Try to convert to string as fallback
    #             else:
    #                 try:
    #                     json.dumps(value)  # Test if serializable
    #                     clean[key] = value
    #                 except:
    #                     continue  # Skip non-serializable
    #         except:
    #             continue  # Skip any problematic keys
    
    #     return clean


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