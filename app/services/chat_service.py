# app/services/chat_service.py
"""
Chat Service - Async Version

All database operations use async SQLAlchemy 2.0 patterns.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, and_
from sqlalchemy.orm import selectinload
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
import json

from app.models.chat import ChatSession
from app.models.message import Message, MessageRole


class ChatService:
    """Business logic for chat operations - Async version"""
    
    @staticmethod
    def _clean_metadata(metadata) -> Optional[Dict]:
        """Remove non-serializable objects from metadata."""
        if not metadata:
            return None
        
        if not isinstance(metadata, dict):
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except:
                    return {}
            else:
                return {}
        
        clean = {}
        for key, value in metadata.items():
            try:
                json.dumps(value)
                clean[key] = value
            except (TypeError, ValueError):
                continue
        return clean
    
    @staticmethod
    async def create_chat(db: AsyncSession, user_id: str, title: str = "New Chat") -> ChatSession:
        """Create a new chat session."""
        chat = ChatSession(user_id=user_id, title=title)
        db.add(chat)
        await db.commit()
        await db.refresh(chat)
        return chat
    
    @staticmethod
    async def get_chat(db: AsyncSession, chat_id: str, user_id: str) -> Optional[ChatSession]:
        """Get chat by ID, ensuring user ownership."""
        result = await db.execute(
            select(ChatSession).where(
                ChatSession.id == chat_id,
                ChatSession.user_id == user_id
            )
        )
        return result.scalar_one_or_none()
    
    @staticmethod
    async def get_chat_admin(db: AsyncSession, chat_id: str) -> Optional[ChatSession]:
        """Get chat by ID (admin access, no user check)."""
        result = await db.execute(
            select(ChatSession).where(ChatSession.id == chat_id)
        )
        return result.scalar_one_or_none()
    
    @staticmethod
    async def list_user_chats(
        db: AsyncSession,
        user_id: str,
        skip: int = 0,
        limit: int = 20,
        include_deleted: bool = False
    ) -> Dict[str, Any]:
        """List user's chat sessions."""
        query = select(ChatSession).where(ChatSession.user_id == user_id)
        
        if not include_deleted:
            query = query.where(ChatSession.is_deleted == False)
        
        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        count_result = await db.execute(count_query)
        total = count_result.scalar() or 0
        
        # Get chats with pagination
        query = query.order_by(desc(ChatSession.updated_at)).offset(skip).limit(limit)
        result = await db.execute(query)
        chats = result.scalars().all()
        
        result_chats = []
        for chat in chats:
            # Get message count
            count_result = await db.execute(
                select(func.count()).select_from(Message).where(
                    Message.chat_session_id == chat.id
                )
            )
            message_count = count_result.scalar() or 0
            
            # Get last message
            last_msg_result = await db.execute(
                select(Message).where(
                    Message.chat_session_id == chat.id
                ).order_by(desc(Message.created_at)).limit(1)
            )
            last_message = last_msg_result.scalar_one_or_none()
            
            chat_dict = {
                "id": chat.id,
                "user_id": chat.user_id,
                "title": chat.title,
                "is_deleted": chat.is_deleted,
                "created_at": chat.created_at,
                "updated_at": chat.updated_at,
                "message_count": message_count,
                "last_message_at": last_message.created_at if last_message else None
            }
            result_chats.append(chat_dict)
        
        return {
            "total": total,
            "chats": result_chats,
            "skip": skip,
            "limit": limit
        }
    
    @staticmethod
    async def update_chat_title(
        db: AsyncSession, 
        chat_id: str, 
        user_id: str, 
        new_title: str
    ) -> Optional[ChatSession]:
        """Update chat title."""
        chat = await ChatService.get_chat(db, chat_id, user_id)
        if not chat:
            return None
        
        chat.title = new_title
        chat.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(chat)
        return chat
    
    @staticmethod
    async def soft_delete_chat(db: AsyncSession, chat_id: str, user_id: str) -> bool:
        """Soft delete a chat."""
        chat = await ChatService.get_chat(db, chat_id, user_id)
        if not chat:
            return False
        
        chat.is_deleted = True
        chat.deleted_at = datetime.now(timezone.utc)
        await db.commit()
        return True
    
    @staticmethod
    async def restore_chat(db: AsyncSession, chat_id: str, user_id: str) -> bool:
        """Restore a soft-deleted chat."""
        result = await db.execute(
            select(ChatSession).where(
                ChatSession.id == chat_id,
                ChatSession.user_id == user_id,
                ChatSession.is_deleted == True
            )
        )
        chat = result.scalar_one_or_none()
        
        if not chat:
            return False
        
        chat.is_deleted = False
        chat.deleted_at = None
        await db.commit()
        return True
    
    @staticmethod
    async def add_message(
        db: AsyncSession,
        chat_id: str,
        role: MessageRole,
        content: str,
        usage: Optional[dict] = None,
        metadata: Optional[dict] = None
    ) -> Message:
        """Add a message to a chat with proper ordering."""
        cleaned_metadata = ChatService._clean_metadata(metadata)
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Get max order index with FOR UPDATE lock
                result = await db.execute(
                    select(func.max(Message.order_index)).where(
                        Message.chat_session_id == chat_id
                    ).with_for_update()
                )
                max_order = result.scalar() or 0
                order_index = max_order + 1
                
                message = Message(
                    chat_session_id=chat_id,
                    role=role,
                    content=content,
                    usage=usage,
                    order_index=order_index,
                    meta_data=cleaned_metadata
                )
                db.add(message)
                
                # Update chat's updated_at
                result = await db.execute(
                    select(ChatSession).where(ChatSession.id == chat_id)
                )
                chat = result.scalar_one_or_none()
                if chat:
                    chat.updated_at = datetime.now(timezone.utc)
                
                await db.commit()
                await db.refresh(message)
                return message
                
            except Exception as e:
                await db.rollback()
                if attempt == max_retries - 1:
                    raise
                continue
        
        raise Exception("Failed to add message after retries")
    
    @staticmethod
    async def get_messages(
        db: AsyncSession,
        chat_id: str,
        skip: int = 0,
        limit: int = 50
    ) -> List[Message]:
        """Get messages for a chat."""
        result = await db.execute(
            select(Message).where(
                Message.chat_session_id == chat_id
            ).order_by(Message.order_index).offset(skip).limit(limit)
        )
        return list(result.scalars().all())
    
    @staticmethod
    def auto_generate_title(content: str, max_length: int = 50) -> str:
        """Auto-generate chat title from first message."""
        title = content.strip()[:max_length]
        if len(content) > max_length:
            title += "..."
        return title if title else "New Chat"
    
    @staticmethod
    async def export_conversation(db: AsyncSession, chat_id: str) -> Optional[Dict]:
        """Export full conversation for admin."""
        result = await db.execute(
            select(ChatSession).where(ChatSession.id == chat_id)
        )
        chat = result.scalar_one_or_none()
        if not chat:
            return None
        
        result = await db.execute(
            select(Message).where(
                Message.chat_session_id == chat_id
            ).order_by(Message.order_index)
        )
        messages = result.scalars().all()
        
        return {
            "user_id": chat.user_id,
            "chat_id": chat.id,
            "title": chat.title,
            "created_at": chat.created_at,
            "message_count": len(messages),
            "messages": [
                {
                    "role": msg.role.value,
                    "content": msg.content,
                    "usage": msg.usage,
                    "metadata": msg.meta_data,
                    "created_at": msg.created_at.isoformat()
                }
                for msg in messages
            ]
        }