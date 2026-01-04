from sqlalchemy.orm import Session
from sqlalchemy import func, and_, desc
from typing import Optional, List
from datetime import datetime, timedelta, timezone
import json
from app.models.chat import ChatSession
from app.models.message import Message, MessageRole


class ChatService:
    """Business logic for chat operations"""
    @staticmethod
    def _clean_metadata(metadata):
        """Remove non-serializable objects from metadata"""
        if not metadata:
            return None
        
        # Force to dict if it's not
        if not isinstance(metadata, dict):
            # If it's a string, parse it
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except:
                    return {}
            else:
                return {}
        
        # Recursively clean
        import json as json_lib
        clean = {}
        for key, value in metadata.items():
            try:
                # Test if value is JSON serializable
                json_lib.dumps(value)
                clean[key] = value
            except (TypeError, ValueError):
                # Skip non-serializable values
                continue
        return clean
    
    @staticmethod
    def create_chat(db: Session, user_id: str, title: str = "New Chat") -> ChatSession:
        """Create a new chat session"""
        chat = ChatSession(
            user_id=user_id,
            title=title
        )
        db.add(chat)
        db.commit()
        db.refresh(chat)
        return chat
    
    @staticmethod
    def get_chat(db: Session, chat_id: str, user_id: str) -> Optional[ChatSession]:
        """Get chat by ID, ensuring user ownership"""
        return db.query(ChatSession).filter(
            ChatSession.id == chat_id,
            ChatSession.user_id == user_id
        ).first()
    
    @staticmethod
    def get_chat_admin(db: Session, chat_id: str) -> Optional[ChatSession]:
        """Get chat by ID (admin access, no user check)"""
        return db.query(ChatSession).filter(ChatSession.id == chat_id).first()
    
    @staticmethod
    def list_user_chats(
        db: Session,
        user_id: str,
        skip: int = 0,
        limit: int = 20,
        include_deleted: bool = False
    ):
        """List user's chat sessions"""
        query = db.query(ChatSession).filter(ChatSession.user_id == user_id)
        
        if not include_deleted:
            query = query.filter(ChatSession.is_deleted == False)
        
        # Order by most recent activity
        query = query.order_by(desc(ChatSession.updated_at))
        
        total = query.count()
        chats = query.offset(skip).limit(limit).all()
        
        # Add message count and last message time
        result_chats = []
        for chat in chats:
            message_count = db.query(func.count(Message.id)).filter(
                Message.chat_session_id == chat.id
            ).scalar()
            
            last_message = db.query(Message).filter(
                Message.chat_session_id == chat.id
            ).order_by(desc(Message.created_at)).first()
            
            chat_dict = {
                "id": chat.id,
                "user_id": chat.user_id,
                "title": chat.title,
                "is_deleted": chat.is_deleted,
                "created_at": chat.created_at,
                "updated_at": chat.updated_at,
                "message_count": message_count or 0,
                "last_message_at": last_message.created_at if last_message else None
            }
            result_chats.append(chat_dict)
        
        return {
            "total": total,
            "chats": result_chats,
            "skip": skip,
            "limit": limit # limit value inserted from query
        }
    
    @staticmethod
    def update_chat_title(db: Session, chat_id: str, user_id: str, new_title: str) -> Optional[ChatSession]:
        """Update chat title"""
        chat = ChatService.get_chat(db, chat_id, user_id)
        if not chat:
            return None
        
        chat.title = new_title
        chat.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(chat)
        return chat
    
    @staticmethod
    def soft_delete_chat(db: Session, chat_id: str, user_id: str) -> bool:
        """Soft delete a chat (mark as deleted, keep data)"""
        chat = ChatService.get_chat(db, chat_id, user_id)
        if not chat:
            return False
        
        chat.is_deleted = True
        chat.deleted_at = datetime.now(timezone.utc)
        db.commit()
        return True
    
    @staticmethod
    def restore_chat(db: Session, chat_id: str, user_id: str) -> bool:
        """Restore a soft-deleted chat"""
        chat = db.query(ChatSession).filter(
            ChatSession.id == chat_id,
            ChatSession.user_id == user_id,
            ChatSession.is_deleted == True
        ).first()
        
        if not chat:
            return False
        
        chat.is_deleted = False
        chat.deleted_at = None
        db.commit()
        return True
    
    @staticmethod
    def add_message(
        db: Session,
        chat_id: str,
        role: MessageRole,
        content: str,
        usage: Optional[dict] = None,
        metadata: Optional[dict] = None
    ) -> Message:
        """Add a message to a chat"""
        # Get next order index
        max_order = db.query(func.max(Message.order_index)).filter(
            Message.chat_session_id == chat_id
        ).scalar()
        
        order_index = (max_order or 0) + 1
        
        # Clean metadata before saving
        cleaned_metadata = ChatService._clean_metadata(metadata)
        
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
        chat = db.query(ChatSession).filter(ChatSession.id == chat_id).first()
        if chat:
            chat.updated_at = datetime.now(timezone.utc)
        
        db.commit()
        db.refresh(message)
        return message
    
    @staticmethod
    def get_messages(
        db: Session,
        chat_id: str,
        skip: int = 0,
        limit: int = 50
    ) -> List[Message]:
        """Get messages for a chat"""
        return db.query(Message).filter(
            Message.chat_session_id == chat_id
        ).order_by(Message.order_index).offset(skip).limit(limit).all()
    
    @staticmethod
    def auto_generate_title(content: str, max_length: int = 50) -> str:
        """Auto-generate chat title from first message"""
        # Clean and truncate
        title = content.strip()[:max_length]
        if len(content) > max_length:
            title += "..."
        return title if title else "New Chat"
    
    @staticmethod
    def export_conversation(db: Session, chat_id: str) -> dict:
        """Export full conversation for admin"""
        chat = db.query(ChatSession).filter(ChatSession.id == chat_id).first()
        if not chat:
            return None
        
        messages = db.query(Message).filter(
            Message.chat_session_id == chat_id
        ).order_by(Message.order_index).all()
        
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
                    "usage": msg.usage ,
                    "metadata": msg.meta_data,
                    "created_at": msg.created_at.isoformat()
                }
                for msg in messages
            ]
        }