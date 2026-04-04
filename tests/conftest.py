"""
Pytest configuration and fixtures for RAG Service tests.

Fully async test infrastructure matching the async application.

KEY DESIGN:
1. asyncio_mode = auto — no need for @pytest.mark.asyncio or @pytest_asyncio.fixture
2. DB fixture uses SAVEPOINT so service commits don't break rollback
3. All async fixtures are plain @pytest.fixture — auto mode handles them
4. Session-scoped event_loop prevents "Event loop is closed" errors
"""
import pytest
import os
import sys
import uuid
import asyncio
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any
from sqlalchemy.ext.asyncio import (
    create_async_engine, AsyncSession, async_sessionmaker, AsyncEngine
)
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))


# =============================================================================
# DATABASE CONFIGURATION
# =============================================================================

MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = os.getenv("MYSQL_PORT", "3306")
MYSQL_USER = os.getenv("MYSQL_USER", "raguser")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
MYSQL_ROOT_PASSWORD = os.getenv("MYSQL_ROOT_PASSWORD")
TEST_DATABASE = os.getenv("TEST_MYSQL_DATABASE", "test_ragdb")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_DB = os.getenv("TEST_REDIS_DB", "1")

TEST_ASYNC_DATABASE_URL = (
    f"mysql+asyncmy://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{TEST_DATABASE}"
)
TEST_SYNC_DATABASE_URL = (
    f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{TEST_DATABASE}"
)
ROOT_DATABASE_URL = (
    f"mysql+pymysql://root:{MYSQL_ROOT_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}"
)

if REDIS_PASSWORD:
    TEST_REDIS_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
else:
    TEST_REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"


# =============================================================================
# CONSTANTS
# =============================================================================

TEST_PASSWORD = "TestPassword123!"
ADMIN_PASSWORD = "AdminPassword123!"


# =============================================================================
# HELPER CLASSES
# =============================================================================

@dataclass
class ChatWithMessages:
    chat: Any
    messages: list

    @property
    def id(self):
        return self.chat.id

    @property
    def user_id(self):
        return self.chat.user_id

    @property
    def title(self):
        return self.chat.title


@dataclass
class UserWithChat:
    user: Any
    chat: Any

    def __iter__(self):
        return iter([self.user, self.chat])


# =============================================================================
# PYTEST CONFIGURATION
# =============================================================================

def pytest_configure(config):
    config.addinivalue_line("markers", "unit: Unit tests (no external deps)")
    config.addinivalue_line("markers", "integration: Integration tests (need DB/Redis)")
    config.addinivalue_line("markers", "security: Security tests")
    config.addinivalue_line("markers", "slow: Slow running tests")


# =============================================================================
# SESSION-SCOPED EVENT LOOP
# =============================================================================

@pytest.fixture(scope="session")
def event_loop():
    """
    Create a session-scoped event loop.
    
    This ensures all session-scoped async fixtures (like async_engine)
    use the same event loop that persists for the entire test session.
    Prevents 'Event loop is closed' errors during cleanup.
    """
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


# =============================================================================
# DATABASE SETUP (sync — runs once per session)
# =============================================================================

@pytest.fixture(scope="session")
def setup_test_database():
    try:
        root_engine = create_engine(ROOT_DATABASE_URL, echo=False)
        with root_engine.connect() as conn:
            conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{TEST_DATABASE}`"))
            conn.commit()
            conn.execute(text(f"GRANT ALL PRIVILEGES ON `{TEST_DATABASE}`.* TO '{MYSQL_USER}'@'%%'"))
            conn.execute(text("FLUSH PRIVILEGES"))
            conn.commit()
        root_engine.dispose()
    except Exception as e:
        print(f"    Could not setup as root: {e}")
    yield


@pytest.fixture(scope="session")
def sync_engine(setup_test_database):
    engine = create_engine(TEST_SYNC_DATABASE_URL, echo=False, pool_pre_ping=True)
    yield engine
    engine.dispose()

@pytest.fixture(scope="session")
def tables(sync_engine):
    from app.core.database import Base
    from app.models.user import User
    from app.models.chat import ChatSession
    from app.models.message import Message
    from app.models.credit import MessageCredit          # ← ADD
    from app.payment.models.wallet import Wallet         # ← ADD
    from app.payment.models.wallet import WalletTransaction  # ← ADD

    Base.metadata.create_all(bind=sync_engine)
    yield
    Base.metadata.drop_all(bind=sync_engine)

@pytest.fixture(scope="session")
def async_engine(setup_test_database, event_loop):
    """
    Session-scoped async engine.
    
    Depends on event_loop to ensure the loop exists before engine creation.
    Uses sync_engine.dispose() for cleanup to avoid async issues at shutdown.
    """
    engine = create_async_engine(
        TEST_ASYNC_DATABASE_URL,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        echo=False,
    )
    yield engine
    # Use sync disposal to avoid "Event loop is closed" error
    engine.sync_engine.dispose()


# =============================================================================
# ASYNC DATABASE SESSION WITH SAVEPOINT
#
# This is THE critical fixture. Services call db.commit() internally.
# Without savepoints, commit() would finalize the outer transaction and
# the rollback in teardown would fail with "transaction already committed".
#
# With SAVEPOINT:
# - outer transaction stays open
# - db.commit() commits the savepoint only
# - event listener re-opens a new savepoint after each commit
# - teardown rolls back the outer transaction → everything is undone
# =============================================================================

@pytest.fixture
async def db(async_engine, tables):
    connection = await async_engine.connect()
    transaction = await connection.begin()
    nested = await connection.begin_nested()

    session = AsyncSession(bind=connection, expire_on_commit=False)

    @event.listens_for(session.sync_session, "after_transaction_end")
    def restart_savepoint(sync_session, trans):
        if trans.nested and not trans._parent.nested:
            sync_session.begin_nested()

    yield session

    await session.close()
    try:
        await transaction.rollback()
    except Exception:
        pass
    try:
        await connection.close()
    except Exception:
        pass


# =============================================================================
# REDIS FIXTURES
# =============================================================================

@pytest.fixture
async def redis_client():
    import redis.asyncio as aioredis
    client = aioredis.from_url(TEST_REDIS_URL, encoding="utf-8", decode_responses=True)
    yield client
    try:
        await client.flushdb()
    except Exception:
        pass
    try:
        await client.aclose()
    except Exception:
        pass

@pytest.fixture
def mock_redis():
    mock = AsyncMock()
    _store = {}

    async def mock_set(key, value, ex=None, **kwargs):
        _store[key] = value
        return True

    async def mock_get(key):
        return _store.get(key, None)

    async def mock_exists(key):
        return 1 if key in _store else 0

    async def mock_delete(key):
        return 1 if _store.pop(key, None) is not None else 0

    mock.get = AsyncMock(side_effect=mock_get)
    mock.set = AsyncMock(side_effect=mock_set)
    mock.exists = AsyncMock(side_effect=mock_exists)
    mock.delete = AsyncMock(side_effect=mock_delete)
    mock.incr = AsyncMock(return_value=1)
    mock.expire = AsyncMock(return_value=True)
    mock.flushdb = AsyncMock(return_value=True)
    mock.ping = AsyncMock(return_value=True)

    pipeline_mock = AsyncMock()
    pipeline_mock.incr = AsyncMock(return_value=pipeline_mock)
    pipeline_mock.expire = AsyncMock(return_value=pipeline_mock)
    pipeline_mock.execute = AsyncMock(return_value=[1, True])
    pipeline_mock.__aenter__ = AsyncMock(return_value=pipeline_mock)
    pipeline_mock.__aexit__ = AsyncMock(return_value=None)
    mock.pipeline = MagicMock(return_value=pipeline_mock)

    return mock

# =============================================================================
# MOCK FIXTURES
# =============================================================================

@pytest.fixture
def mock_db():
    mock = AsyncMock(spec=AsyncSession)
    mock.add = MagicMock()
    mock.commit = AsyncMock()
    mock.refresh = AsyncMock()
    mock.execute = AsyncMock()
    mock.rollback = AsyncMock()
    mock.close = AsyncMock()
    return mock


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.id = "test-user-id-123"
    user.email = "test@example.com"
    user.username = "testuser"
    user.hashed_password = "$argon2id$v=19$m=65536,t=3,p=4$abc$def"
    user.is_active = True
    user.is_admin = False
    user.is_verified = True
    user.max_messages_per_day = None
    user.rate_limit_per_minute = None
    user.created_at = datetime.now(timezone.utc)
    user.last_login_at = None
    user.auth_provider = "local"
    user.full_name = "Test User"
    user.avatar_url = None
    user.oauth_id = None
    return user


@pytest.fixture
def mock_admin_user():
    user = MagicMock()
    user.id = "admin-user-id-456"
    user.email = "admin@example.com"
    user.username = "adminuser"
    user.hashed_password = "$argon2id$v=19$m=65536,t=3,p=4$abc$def"
    user.is_active = True
    user.is_admin = True
    user.is_verified = True
    user.max_messages_per_day = None
    user.rate_limit_per_minute = None
    user.created_at = datetime.now(timezone.utc)
    user.last_login_at = None
    return user


# =============================================================================
# PASSWORD FIXTURES
# =============================================================================

@pytest.fixture
def test_password():
    return TEST_PASSWORD


@pytest.fixture
def admin_password():
    return ADMIN_PASSWORD


# =============================================================================
# USER FIXTURES
# =============================================================================

@pytest.fixture
async def test_user(db: AsyncSession):
    from app.models.user import User, AuthProvider
    from app.core.security import get_password_hash

    user = User(
        email="test@example.com",
        username="testuser",
        hashed_password=get_password_hash(TEST_PASSWORD),
        auth_provider=AuthProvider.LOCAL,
        is_active=True,
        is_verified=True,
        is_admin=False,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


@pytest.fixture
async def admin_user(db: AsyncSession):
    from app.models.user import User, AuthProvider
    from app.core.security import get_password_hash

    user = User(
        email="admin@example.com",
        username="adminuser",
        hashed_password=get_password_hash(ADMIN_PASSWORD),
        auth_provider=AuthProvider.LOCAL,
        is_active=True,
        is_verified=True,
        is_admin=True,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


@pytest.fixture
async def inactive_user(db: AsyncSession):
    from app.models.user import User, AuthProvider
    from app.core.security import get_password_hash

    user = User(
        email="inactive@example.com",
        username="inactiveuser",
        hashed_password=get_password_hash(TEST_PASSWORD),
        auth_provider=AuthProvider.LOCAL,
        is_active=False,
        is_verified=True,
        is_admin=False,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


@pytest.fixture
async def other_user(db: AsyncSession):
    from app.models.user import User, AuthProvider
    from app.core.security import get_password_hash

    user = User(
        email="other@example.com",
        username="otheruser",
        hashed_password=get_password_hash(TEST_PASSWORD),
        auth_provider=AuthProvider.LOCAL,
        is_active=True,
        is_verified=True,
        is_admin=False,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


# =============================================================================
# TOKEN / AUTH FIXTURES
# =============================================================================

@pytest.fixture
def user_token(test_user):
    from app.core.security import create_token_pair
    tokens = create_token_pair(user_id=test_user.id, email=test_user.email, is_admin=test_user.is_admin)
    return tokens["access_token"]


@pytest.fixture
def admin_token(admin_user):
    from app.core.security import create_token_pair
    tokens = create_token_pair(user_id=admin_user.id, email=admin_user.email, is_admin=admin_user.is_admin)
    return tokens["access_token"]


@pytest.fixture
def auth_headers(test_user):
    from app.core.security import create_token_pair
    tokens = create_token_pair(user_id=test_user.id, email=test_user.email, is_admin=test_user.is_admin)
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture
def admin_headers(admin_user):
    from app.core.security import create_token_pair
    tokens = create_token_pair(user_id=admin_user.id, email=admin_user.email, is_admin=admin_user.is_admin)
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture
def other_user_headers(other_user):
    from app.core.security import create_token_pair
    tokens = create_token_pair(user_id=other_user.id, email=other_user.email, is_admin=other_user.is_admin)
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# =============================================================================
# HTTP CLIENT FIXTURE
# =============================================================================

@pytest.fixture
async def client(db: AsyncSession, mock_redis):
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.core.database import get_db, get_redis

    async def override_get_db():
        yield db

    async def override_get_redis():
        return mock_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    transport = ASGITransport(app=app)
    ac = AsyncClient(transport=transport, base_url="http://test")

    yield ac

    await ac.aclose()
    app.dependency_overrides.clear()


# =============================================================================
# CHAT FIXTURES
# =============================================================================

@pytest.fixture
async def test_chat(db: AsyncSession, test_user):
    from app.models.chat import ChatSession

    chat = ChatSession(user_id=test_user.id, title="Test Chat")
    db.add(chat)
    await db.flush()
    await db.refresh(chat)
    return chat


@pytest.fixture
async def other_user_chat(db: AsyncSession, other_user):
    from app.models.chat import ChatSession

    chat = ChatSession(user_id=other_user.id, title="Other User's Chat")
    db.add(chat)
    await db.flush()
    await db.refresh(chat)
    return UserWithChat(user=other_user, chat=chat)


@pytest.fixture
async def deleted_chat(db: AsyncSession, test_user):
    from app.models.chat import ChatSession

    chat = ChatSession(
        user_id=test_user.id,
        title="Deleted Chat",
        is_deleted=True,
        deleted_at=datetime.now(timezone.utc),
    )
    db.add(chat)
    await db.flush()
    await db.refresh(chat)
    return chat


@pytest.fixture
async def test_messages(db: AsyncSession, test_chat):
    from app.models.message import Message, MessageRole

    messages = []
    for i, (role, content) in enumerate([
        (MessageRole.USER, "Hello, what is Python?"),
        (MessageRole.ASSISTANT, "Python is a programming language."),
        (MessageRole.USER, "Tell me more about it."),
        (MessageRole.ASSISTANT, "Python is known for its simplicity."),
    ]):
        msg = Message(
            chat_session_id=test_chat.id,
            role=role,
            content=content,
            order_index=i + 1,
        )
        db.add(msg)
        messages.append(msg)
    await db.flush()
    for msg in messages:
        await db.refresh(msg)
    return messages


@pytest.fixture
async def test_chat_with_messages(db: AsyncSession, test_user):
    from app.models.chat import ChatSession
    from app.models.message import Message, MessageRole

    chat = ChatSession(user_id=test_user.id, title="Chat With Messages")
    db.add(chat)
    await db.flush()
    await db.refresh(chat)

    messages = []
    for i, (role, content) in enumerate([
        (MessageRole.USER, "First question"),
        (MessageRole.ASSISTANT, "First answer"),
        (MessageRole.USER, "Second question"),
        (MessageRole.ASSISTANT, "Second answer"),
    ]):
        msg = Message(
            chat_session_id=chat.id,
            role=role,
            content=content,
            order_index=i + 1,
        )
        db.add(msg)
        messages.append(msg)
    await db.flush()
    for msg in messages:
        await db.refresh(msg)
    return ChatWithMessages(chat=chat, messages=messages)


# =============================================================================
# RAG ENGINE MOCK
# =============================================================================

@pytest.fixture
def mock_rag_engine():
    mock = MagicMock()
    mock.query = AsyncMock(return_value={
        "question": "test question",
        "enhanced_query": "enhanced test question",
        "answer": "This is a test answer from the RAG engine.",
        "usage": {},
        "retrieved_docs": [
            {"content": "Test document content", "metadata": {"source": "test"}}
        ],
        "had_conversation_context": False,
    })
    mock.get_stats = MagicMock(return_value={
        "model": "test-model",
        "index": "test-index",
        "documents_count": 100,
        "device": "cpu",
        "async": True,
    })
    return mock

# =============================================================================
# CREDIT FIXTURES
# =============================================================================

@pytest.fixture
async def test_credit(db: AsyncSession, test_user):
    """
    MessageCredit for test_user with FREE_MESSAGES_FOR_NEW_USERS remaining.
    Use this when you need a user who already has a credit record.
    """
    from app.models.credit import MessageCredit
    from app.config import settings

    credit = MessageCredit(
        user_id=test_user.id,
        remaining=settings.FREE_MESSAGES_FOR_NEW_USERS,
        total_purchased=0,
        total_used=0,
        rejected_count=0,
    )
    db.add(credit)
    await db.flush()
    await db.refresh(credit)
    return credit


@pytest.fixture
async def zero_credit_user(db: AsyncSession, test_user):
    """
    test_user with 0 remaining credits.
    Use for: consume on empty, rejection on empty, must-purchase scenarios.
    """
    from app.models.credit import MessageCredit
    from app.config import settings

    credit = MessageCredit(
        user_id=test_user.id,
        remaining=0,
        total_purchased=0,
        total_used=settings.FREE_MESSAGES_FOR_NEW_USERS,
        rejected_count=0,
    )
    db.add(credit)
    await db.flush()
    await db.refresh(credit)
    return credit


@pytest.fixture
async def at_max_rejections_credit(db: AsyncSession, test_user):
    """
    test_user whose rejected_count == MAX_FREE_REJECTIONS exactly.
    The NEXT rejection call must charge a credit.
    """
    from app.models.credit import MessageCredit
    from app.config import settings

    credit = MessageCredit(
        user_id=test_user.id,
        remaining=settings.FREE_MESSAGES_FOR_NEW_USERS,
        total_purchased=0,
        total_used=0,
        rejected_count=settings.MAX_FREE_REJECTIONS,
    )
    db.add(credit)
    await db.flush()
    await db.refresh(credit)
    return credit


# =============================================================================
# WALLET FIXTURES
# =============================================================================

@pytest.fixture
async def test_wallet(db: AsyncSession, test_user):
    """
    Wallet for test_user with 10x MAX purchase amount.
    Use for: any test where purchase should succeed.
    """
    import uuid as _uuid
    from app.payment.models.wallet import Wallet
    from app.config import settings

    wallet = Wallet(
        id=str(_uuid.uuid4()),
        user_id=test_user.id,
        balance=settings.MAX_MESSAGE_PURCHASE * settings.PRICE_PER_MESSAGE * 10,
    )
    db.add(wallet)
    await db.flush()
    await db.refresh(wallet)
    return wallet


@pytest.fixture
async def empty_wallet(db: AsyncSession, test_user):
    """
    Wallet for test_user with 0 balance.
    Use for: insufficient balance → 402 tests.
    """
    import uuid as _uuid
    from app.payment.models.wallet import Wallet

    wallet = Wallet(
        id=str(_uuid.uuid4()),
        user_id=test_user.id,
        balance=0,
    )
    db.add(wallet)
    await db.flush()
    await db.refresh(wallet)
    return wallet


@pytest.fixture
async def exact_balance_wallet(db: AsyncSession, test_user):
    """
    Wallet with balance == MIN_MESSAGE_PURCHASE * PRICE_PER_MESSAGE exactly.
    Boundary test: balance == required → must PASS (strict less-than check).
    """
    import uuid as _uuid
    from app.payment.models.wallet import Wallet
    from app.config import settings

    exact = settings.MIN_MESSAGE_PURCHASE * settings.PRICE_PER_MESSAGE
    wallet = Wallet(
        id=str(_uuid.uuid4()),
        user_id=test_user.id,
        balance=exact,
    )
    db.add(wallet)
    await db.flush()
    await db.refresh(wallet)
    return wallet


@pytest.fixture
async def one_short_wallet(db: AsyncSession, test_user):
    """
    Wallet with balance == (MIN_MESSAGE_PURCHASE * PRICE_PER_MESSAGE) - 1.
    Boundary test: one Rial short → must FAIL with 402.
    """
    import uuid as _uuid
    from app.payment.models.wallet import Wallet
    from app.config import settings

    one_short = (settings.MIN_MESSAGE_PURCHASE * settings.PRICE_PER_MESSAGE) - 1
    wallet = Wallet(
        id=str(_uuid.uuid4()),
        user_id=test_user.id,
        balance=one_short,
    )
    db.add(wallet)
    await db.flush()
    await db.refresh(wallet)
    return wallet