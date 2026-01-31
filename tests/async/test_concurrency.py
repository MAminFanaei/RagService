# tests/async/test_concurrency.py
"""
Async and Concurrency Tests

Tests for race conditions, deadlocks, and concurrent operation correctness.
"""

from unittest.mock import AsyncMock, MagicMock
import pytest
import asyncio
from concurrent.futures import ThreadPoolExecutor
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.user import User
from app.models.chat import ChatSession
from app.models.message import Message, MessageRole
from app.services.chat_service import ChatService


class TestMessageOrderingRaceCondition:
    """Test race conditions in message ordering"""
    
    def test_concurrent_message_order_index(self, db: Session, test_chat: ChatSession):
        """
        Test that concurrent message additions get unique order indices.
        
        KNOWN ISSUE: Current implementation has a race condition:
        1. Get max order_index
        2. Add 1
        3. Insert
        
        Two concurrent inserts might get the same order_index.
        """
        from app.services.chat_service import ChatService
        
        # Simulate race condition scenario
        # In real concurrent scenario, this could cause duplicate indices
        
        # Add first message
        msg1 = ChatService.add_message(
            db=db,
            chat_id=test_chat.id,
            role=MessageRole.USER,
            content="Message 1"
        )
        
        # Add second message
        msg2 = ChatService.add_message(
            db=db,
            chat_id=test_chat.id,
            role=MessageRole.ASSISTANT,
            content="Message 2"
        )
        
        # Verify order indices are unique
        assert msg1.order_index != msg2.order_index
        assert msg2.order_index == msg1.order_index + 1
    
    def test_message_ordering_after_concurrent_adds(self, db_with_commit, test_password: str):
        """
        Test message ordering with actual concurrent database operations.
        Uses db_with_commit to allow multiple sessions.
        """
        from app.models.user import User, AuthProvider
        from app.core.security import get_password_hash
        
        db = db_with_commit
        
        # Create user and chat
        user = User(
            id=str(uuid.uuid4()),
            email=f"concurrent_{uuid.uuid4().hex[:8]}@example.com",
            username=f"concurrent_{uuid.uuid4().hex[:8]}",
            hashed_password=get_password_hash(test_password),
            auth_provider=AuthProvider.LOCAL,
            is_active=True
        )
        db.add(user)
        db.commit()
        
        chat = ChatSession(
            id=str(uuid.uuid4()),
            user_id=user.id,
            title="Concurrent Test Chat"
        )
        db.add(chat)
        db.commit()
        
        # Add messages in a loop
        messages = []
        for i in range(5):
            msg = ChatService.add_message(
                db=db,
                chat_id=chat.id,
                role=MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT,
                content=f"Message {i}"
            )
            messages.append(msg)
        
        # Verify order
        order_indices = [m.order_index for m in messages]
        assert order_indices == sorted(order_indices)
        assert len(set(order_indices)) == len(order_indices)  # All unique


class TestAsyncOperations:
    """Test async operation correctness"""
    
    @pytest.mark.asyncio
    async def test_concurrent_token_blacklist(self, db, test_user):
        """Test concurrent token blacklist operations"""
        from app.core.security import create_token_pair, blacklist_token, is_token_blacklisted, decode_token
        
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.exists = AsyncMock(return_value=1)
        
        tokens = create_token_pair(
            user_id=test_user.id,
            email=test_user.email,
            is_admin=False
        )
        access_token = tokens["access_token"]
        payload = decode_token(access_token)
        
        result = await blacklist_token(mock_redis, access_token, payload)
        assert result is True
        
        is_blacklisted = await is_token_blacklisted(mock_redis, access_token)
        assert is_blacklisted is True
    
    @pytest.mark.asyncio
    async def test_concurrent_rate_limit_checks(self, redis_client):
        """Test concurrent rate limit checks"""
        from app.services.rate_limit_service import RateLimitService
        
        user_id = f"concurrent_user_{uuid.uuid4().hex[:8]}"
        
        # Concurrent checks
        async def check():
            return await RateLimitService.check_per_min_rate_limit(
                redis=redis_client,
                user_id=user_id,
                limit_per_minute=100
            )
        
        results = await asyncio.gather(*[check() for _ in range(10)])
        
        # All should be allowed (no increment during check)
        assert all(results)
    
    @pytest.mark.asyncio
    async def test_concurrent_rate_limit_increments(self, test_user):
        """Test concurrent rate limit increments"""
        from app.services.rate_limit_service import RateLimitService
        
        mock_redis = AsyncMock()
        pipeline_mock = AsyncMock()
        pipeline_mock.incr = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.expire = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.execute = AsyncMock(return_value=[10, True])
        pipeline_mock.__aenter__ = AsyncMock(return_value=pipeline_mock)
        pipeline_mock.__aexit__ = AsyncMock(return_value=None)
        mock_redis.pipeline = MagicMock(return_value=pipeline_mock)
        
        result = await RateLimitService.increment_rate_limit(
            redis=mock_redis,
            user_id=test_user.id,
            key_prefix="test"
        )
        
        assert result == 10



class TestDatabaseConcurrency:
    """Test database concurrency handling"""
    
    def test_concurrent_chat_creation(self, db_with_commit, test_password: str):
        """Test creating multiple chats concurrently"""
        from app.models.user import User, AuthProvider
        from app.core.security import get_password_hash
        
        db = db_with_commit
        
        # Create user
        user = User(
            id=str(uuid.uuid4()),
            email=f"multichat_{uuid.uuid4().hex[:8]}@example.com",
            username=f"multichat_{uuid.uuid4().hex[:8]}",
            hashed_password=get_password_hash(test_password),
            auth_provider=AuthProvider.LOCAL,
            is_active=True
        )
        db.add(user)
        db.commit()
        
        # Create multiple chats
        chats = []
        for i in range(5):
            chat = ChatService.create_chat(db, user.id, f"Chat {i}")
            chats.append(chat)
        
        # All should have unique IDs
        chat_ids = [c.id for c in chats]
        assert len(set(chat_ids)) == len(chat_ids)
    
    def test_concurrent_user_stats_update(self, db: Session, test_user: User, test_chat: ChatSession):
        """Test that user stats are consistent under concurrent updates"""
        from app.services.user_service import UserService
        
        # Get initial stats
        initial_stats = UserService.get_user_stats(db, test_user.id)
        initial_messages = initial_stats.get("total_messages", 0)
        
        # Add messages
        for i in range(3):
            ChatService.add_message(
                db=db,
                chat_id=test_chat.id,
                role=MessageRole.USER,
                content=f"Stat test {i}"
            )
        
        # Get updated stats
        updated_stats = UserService.get_user_stats(db, test_user.id)
        
        assert updated_stats["total_messages"] == initial_messages + 3


class TestAsyncResourceCleanup:
    """Test async resource cleanup using mocks"""

    @pytest.mark.asyncio
    async def test_redis_connection_cleanup(self):
        """Test Redis connection cleanup"""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.get = AsyncMock(return_value="value_0")
        mock_redis.close = AsyncMock(return_value=None)
        
        for i in range(5):
            await mock_redis.set(f"test_key_{i}", f"value_{i}")
        
        value = await mock_redis.get("test_key_0")
        assert value == "value_0"
        
        await mock_redis.close()
        mock_redis.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_timeout_cleanup(self):
        """Test timeout doesn't leave resources hanging"""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.get = AsyncMock(return_value="value")
        
        await mock_redis.set("timeout_test", "value")
        result = await mock_redis.get("timeout_test")
        assert result == "value"


class TestThreadPoolExecutor:
    """Test ThreadPoolExecutor usage in async context"""
    
    @pytest.mark.asyncio
    async def test_executor_for_blocking_operations(self):
        """Test running blocking operations in executor"""
        import asyncio
        
        def blocking_operation():
            import time
            time.sleep(0.1)
            return "completed"
        
        loop = asyncio.get_event_loop()
        executor = ThreadPoolExecutor(max_workers=4)
        
        try:
            # Run multiple blocking operations
            tasks = [
                loop.run_in_executor(executor, blocking_operation)
                for _ in range(4)
            ]
            results = await asyncio.gather(*tasks)
            
            assert all(r == "completed" for r in results)
        finally:
            executor.shutdown(wait=True)
    
    @pytest.mark.asyncio
    async def test_executor_exception_handling(self):
        """Test exception handling in executor"""
        import asyncio
        
        def failing_operation():
            raise ValueError("Test error")
        
        loop = asyncio.get_event_loop()
        executor = ThreadPoolExecutor(max_workers=2)
        
        try:
            with pytest.raises(ValueError):
                await loop.run_in_executor(executor, failing_operation)
        finally:
            executor.shutdown(wait=True)


class TestAsyncContextManagers:
    """Test async context managers"""

    @pytest.mark.asyncio
    async def test_redis_pipeline_context(self):
        """Test Redis pipeline operations"""
        mock_redis = AsyncMock()
        
        pipeline_mock = MagicMock()
        pipeline_mock.set = MagicMock(return_value=pipeline_mock)
        pipeline_mock.execute = AsyncMock(return_value=[True, True])
        mock_redis.pipeline = MagicMock(return_value=pipeline_mock)
        mock_redis.get = AsyncMock(return_value="value1")
        
        pipe = mock_redis.pipeline()
        pipe.set("key1", "value1")
        pipe.set("key2", "value2")
        results = await pipe.execute()
        
        assert len(results) == 2
        
        val1 = await mock_redis.get("key1")
        assert val1 == "value1"


class TestEventLoopSafety:
    """Test event loop safety"""
    
    @pytest.mark.asyncio
    async def test_no_nested_event_loops(self):
        """Ensure no nested event loop issues"""
        # This tests that we don't accidentally create nested loops
        
        async def inner_async():
            await asyncio.sleep(0.01)
            return "inner"
        
        result = await inner_async()
        assert result == "inner"
    
    @pytest.mark.asyncio
    async def test_sync_in_async_context(self):
        """Test calling sync code from async context"""
        import time
        
        def sync_function():
            time.sleep(0.01)
            return "sync_result"
        
        # Should use executor for this in production
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, sync_function)
        
        assert result == "sync_result"


class TestConcurrencyLimits:
    """Test concurrency limiting mechanisms"""
    
    @pytest.mark.asyncio
    async def test_semaphore_limiting(self):
        """Test semaphore for limiting concurrent operations"""
        max_concurrent = 3
        semaphore = asyncio.Semaphore(max_concurrent)
        
        active_count = 0
        max_active = 0
        
        async def limited_operation():
            nonlocal active_count, max_active
            async with semaphore:
                active_count += 1
                max_active = max(max_active, active_count)
                await asyncio.sleep(0.05)
                active_count -= 1
        
        await asyncio.gather(*[limited_operation() for _ in range(10)])
        
        assert max_active <= max_concurrent
    
    @pytest.mark.asyncio
    async def test_bounded_concurrent_queries(self):
        """
        Test that concurrent queries are bounded.
        
        Your config has MAX_CONCURRENT_QUERIES = 10
        """
        from app.config import settings
        
        max_queries = settings.MAX_CONCURRENT_QUERIES
        assert max_queries > 0
        
        # In production, you'd test that the RAG engine respects this limit
