from app.models.user import User, AuthProvider
from app.models.chat import ChatSession
from app.models.message import Message, MessageRole
from app.models.credit import MessageCredit

__all__ = [
    "User",
    "AuthProvider",
    "ChatSession",
    "Message",
    "MessageRole",
]

# # TO FIX MESSAGE.METADATA problem 
# from .message import Message
# # import other models as needed...

# # Attach backward-compatible alias AFTER the classes are defined/mapped
# def _attach_alias(cls, alias, target):
#     """
#     Attach a property alias `alias` on class `cls` that proxies to attribute `target`.
#     This is done after class creation so SQLAlchemy mapping is not affected.
#     """
#     def _getter(self):
#         return getattr(self, target)

#     def _setter(self, value):
#         setattr(self, target, value)

#     def _deleter(self):
#         delattr(self, target)

#     setattr(cls, alias, property(_getter, _setter, _deleter, 
#                                  doc=f"Alias for '{target}' (backwards compatibility)"))

# # attach alias for Message.metadata -> Message.meta_data
# _attach_alias(Message, "metadata", "meta_data")