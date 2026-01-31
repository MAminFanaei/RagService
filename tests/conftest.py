"""
Pytest configuration and fixtures for RAG Service tests.

Uses the MAIN project infrastructure (MySQL, Redis) with a separate test database.
"""
import pytest
import os
import sys
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime, timezone
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Load the main project's .env file
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))


# =============================================================================
# READ FROM MAIN .env - USE SAME INFRASTRUCTURE, DIFFERENT DATABASE
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

TEST_DATABASE_URL = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{TEST_DATABASE}"
ROOT_DATABASE_URL = f"mysql+pymysql://root:{MYSQL_ROOT_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}"

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
# PYTEST CONFIGURATION
# =============================================================================

def pytest_configure(config):
    """Print test configuration and register markers"""
    print("\n" + "=" * 60)
    print("TEST CONFIGURATION")
    print("=" * 60)
    print(f"MySQL Host: {MYSQL_HOST}:{MYSQL_PORT}")
    print(f"MySQL User: {MYSQL_USER}")
    print(f"Test Database: {TEST_DATABASE}")
    print(f"Redis: {REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}")
    print("=" * 60 + "\n")
    
    config.addinivalue_line("markers", "unit: Unit tests (no external deps)")
    config.addinivalue_line("markers", "integration: Integration tests (need DB/Redis)")
    config.addinivalue_line("markers", "security: Security tests")


# =============================================================================
# DATABASE SETUP
# =============================================================================

@pytest.fixture(scope="session")
def setup_test_database():
    """Create test database if it doesn't exist."""
    print(f"\n>>> Setting up test database: {TEST_DATABASE}")
    
    try:
        root_engine = create_engine(ROOT_DATABASE_URL, echo=False)
        
        with root_engine.connect() as conn:
            conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{TEST_DATABASE}`"))
            conn.commit()
            conn.execute(text(f"GRANT ALL PRIVILEGES ON `{TEST_DATABASE}`.* TO '{MYSQL_USER}'@'%'"))
            conn.execute(text(f"GRANT ALL PRIVILEGES ON `{TEST_DATABASE}`.* TO '{MYSQL_USER}'@'localhost'"))
            conn.execute(text("FLUSH PRIVILEGES"))
            conn.commit()
            print(f"    ✓ Database '{TEST_DATABASE}' ready")
        
        root_engine.dispose()
        
    except Exception as e:
        print(f"    ⚠ Could not setup as root: {e}")
    
    yield


@pytest.fixture(scope="session")
def engine(setup_test_database):
    """Create SQLAlchemy engine for test database"""
    engine = create_engine(
        TEST_DATABASE_URL,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        echo=False
    )
    
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            print("    ✓ Database connection successful")
    except Exception as e:
        print(f"    ✗ Database connection FAILED: {e}")
        raise
    
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def tables(engine):
    """Create all tables once per test session"""
    from app.core.database import Base
    from app.models.user import User
    from app.models.chat import ChatSession
    from app.models.message import Message
    
    print("\n>>> Creating tables...")
    Base.metadata.create_all(bind=engine)
    print("    ✓ Tables created")
    
    yield
    
    print("\n>>> Dropping tables...")
    Base.metadata.drop_all(bind=engine)
    print("    ✓ Tables dropped")


@pytest.fixture
def db(engine, tables):
    """Database session with transaction rollback for isolation"""
    connection = engine.connect()
    transaction = connection.begin()
    
    Session = sessionmaker(bind=connection)
    session = Session()
    
    yield session
    
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def db_committed(engine, tables):
    """Database session WITHOUT rollback - for tests that need committed data"""
    from app.core.database import Base
    
    Session = sessionmaker(bind=engine)
    session = Session()
    
    yield session
    
    # Clean up all tables after test
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    session.close()


# =============================================================================
# REDIS FIXTURES
# =============================================================================

@pytest.fixture
def redis_client():
    """Real Redis client - synchronous wrapper for async redis"""
    import redis
    
    client = redis.from_url(
        TEST_REDIS_URL.replace("redis://", "redis://"),
        encoding="utf-8",
        decode_responses=True
    )
    
    yield client
    
    client.flushdb()
    client.close()


@pytest.fixture
def mock_redis():
    """Mock Redis client for unit tests"""
    mock = AsyncMock()
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock(return_value=True)
    mock.incr = AsyncMock(return_value=1)
    mock.expire = AsyncMock(return_value=True)
    mock.exists = AsyncMock(return_value=0)
    mock.delete = AsyncMock(return_value=1)
    mock.flushdb = AsyncMock(return_value=True)
    
    pipeline_mock = AsyncMock()
    pipeline_mock.incr = AsyncMock(return_value=pipeline_mock)
    pipeline_mock.expire = AsyncMock(return_value=pipeline_mock)
    pipeline_mock.execute = AsyncMock(return_value=[1, True])
    pipeline_mock.__aenter__ = AsyncMock(return_value=pipeline_mock)
    pipeline_mock.__aexit__ = AsyncMock(return_value=None)
    mock.pipeline = MagicMock(return_value=pipeline_mock)
    
    return mock


# =============================================================================
# MOCK FIXTURES (for unit tests)
# =============================================================================

@pytest.fixture
def mock_db():
    """Mock database session for unit tests"""
    mock = MagicMock()
    mock.add = MagicMock()
    mock.commit = MagicMock()
    mock.refresh = MagicMock()
    mock.query = MagicMock()
    mock.delete = MagicMock()
    mock.rollback = MagicMock()
    mock.close = MagicMock()
    return mock


@pytest.fixture
def mock_user():
    """Mock user object for unit tests"""
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
    return user


@pytest.fixture
def mock_admin_user():
    """Mock admin user for unit tests"""
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
# PASSWORD FIXTURE
# =============================================================================

@pytest.fixture
def test_password():
    """Standard test password"""
    return TEST_PASSWORD


# =============================================================================
# USER FIXTURES (for integration tests)
# =============================================================================

@pytest.fixture
def test_user(db):
    """Create a real test user in database"""
    from app.models.user import User, AuthProvider
    from app.core.security import get_password_hash
    
    user = User(
        email="test@example.com",
        username="testuser",
        hashed_password=get_password_hash(TEST_PASSWORD),
        auth_provider=AuthProvider.LOCAL,
        is_active=True,
        is_verified=True,
        is_admin=False
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def admin_user(db):
    """Create a real admin user in database"""
    from app.models.user import User, AuthProvider
    from app.core.security import get_password_hash
    
    user = User(
        email="admin@example.com",
        username="adminuser",
        hashed_password=get_password_hash(ADMIN_PASSWORD),
        auth_provider=AuthProvider.LOCAL,
        is_active=True,
        is_verified=True,
        is_admin=True
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def inactive_user(db):
    """Create an inactive user"""
    from app.models.user import User, AuthProvider
    from app.core.security import get_password_hash
    
    user = User(
        email="inactive@example.com",
        username="inactiveuser",
        hashed_password=get_password_hash(TEST_PASSWORD),
        auth_provider=AuthProvider.LOCAL,
        is_active=False,
        is_verified=True,
        is_admin=False
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def other_user(db):
    """Create another test user for IDOR tests"""
    from app.models.user import User, AuthProvider
    from app.core.security import get_password_hash
    
    user = User(
        email="other@example.com",
        username="otheruser",
        hashed_password=get_password_hash(TEST_PASSWORD),
        auth_provider=AuthProvider.LOCAL,
        is_active=True,
        is_verified=True,
        is_admin=False
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# =============================================================================
# TOKEN/AUTH FIXTURES
# =============================================================================

@pytest.fixture
def user_token(test_user):
    """Get access token for test user"""
    from app.core.security import create_token_pair
    
    tokens = create_token_pair(
        user_id=test_user.id,
        email=test_user.email,
        is_admin=test_user.is_admin
    )
    return tokens['access_token']


@pytest.fixture
def admin_token(admin_user):
    """Get access token for admin user"""
    from app.core.security import create_token_pair
    
    tokens = create_token_pair(
        user_id=admin_user.id,
        email=admin_user.email,
        is_admin=admin_user.is_admin
    )
    return tokens['access_token']


@pytest.fixture
def auth_headers(test_user):
    """Authorization headers for test user"""
    from app.core.security import create_token_pair
    
    tokens = create_token_pair(
        user_id=test_user.id,
        email=test_user.email,
        is_admin=test_user.is_admin
    )
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture
def user_auth_header(auth_headers):
    """Alias for auth_headers"""
    return auth_headers


@pytest.fixture
def admin_headers(admin_user):
    """Authorization headers for admin user"""
    from app.core.security import create_token_pair
    
    tokens = create_token_pair(
        user_id=admin_user.id,
        email=admin_user.email,
        is_admin=admin_user.is_admin
    )
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture
def admin_auth_header(admin_headers):
    """Alias for admin_headers"""
    return admin_headers


@pytest.fixture
def other_user_headers(other_user):
    """Authorization headers for other user"""
    from app.core.security import create_token_pair
    
    tokens = create_token_pair(
        user_id=other_user.id,
        email=other_user.email,
        is_admin=other_user.is_admin
    )
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# =============================================================================
# HTTP CLIENT FIXTURES
# =============================================================================

@pytest.fixture
def client(db):
    """FastAPI test client with DB dependency override"""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core.database import get_db
    
    def override_get_db():
        try:
            yield db
        finally:
            pass
    
    app.dependency_overrides[get_db] = override_get_db
    
    with TestClient(app) as test_client:
        yield test_client
    
    app.dependency_overrides.clear()


@pytest.fixture
def async_client(db):
    """Async HTTP client for async tests"""
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.core.database import get_db
    
    def override_get_db():
        try:
            yield db
        finally:
            pass
    
    app.dependency_overrides[get_db] = override_get_db
    
    # Create async client with ASGITransport
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    
    yield client
    
    app.dependency_overrides.clear()


# =============================================================================
# CHAT FIXTURES
# =============================================================================

@pytest.fixture
def test_chat(db, test_user):
    """Create a test chat session"""
    from app.models.chat import ChatSession
    
    chat = ChatSession(
        user_id=test_user.id,
        title="Test Chat"
    )
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


@pytest.fixture
def other_user_chat(db, other_user):
    """Create a chat owned by other_user (for IDOR tests)"""
    from app.models.chat import ChatSession
    
    chat = ChatSession(
        user_id=other_user.id,
        title="Other User's Chat"
    )
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


@pytest.fixture
def deleted_chat(db, test_user):
    """Create a soft-deleted chat"""
    from app.models.chat import ChatSession
    
    chat = ChatSession(
        user_id=test_user.id,
        title="Deleted Chat",
        is_deleted=True,
        deleted_at=datetime.now(timezone.utc)
    )
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


@pytest.fixture
def test_messages(db, test_chat):
    """Create test messages in a chat"""
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
            order_index=i + 1
        )
        db.add(msg)
        messages.append(msg)
    
    db.commit()
    for msg in messages:
        db.refresh(msg)
    
    return messages


@pytest.fixture
def test_chat_with_messages(db, test_user):
    """Create a chat with messages already included"""
    from app.models.chat import ChatSession
    from app.models.message import Message, MessageRole
    
    chat = ChatSession(
        user_id=test_user.id,
        title="Chat With Messages"
    )
    db.add(chat)
    db.commit()
    db.refresh(chat)
    
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
            order_index=i + 1
        )
        db.add(msg)
        messages.append(msg)
    
    db.commit()
    
    return {"chat": chat, "messages": messages}


# =============================================================================
# RAG ENGINE MOCK
# =============================================================================

@pytest.fixture
def mock_rag_engine():
    """Mock RAG engine for testing without actual LLM calls"""
    mock = MagicMock()
    mock.query = AsyncMock(return_value={
        "question": "test question",
        "enhanced_query": "enhanced test question",
        "answer": "This is a test answer from the RAG engine.",
        "usage": {},
        "retrieved_docs": [
            {"content": "Test document content", "metadata": {"source": "test"}}
        ],
        "had_conversation_context": False
    })
    mock.get_stats = MagicMock(return_value={
        "model": "test-model",
        "index": "test-index",
        "documents_count": 100,
        "device": "cpu"
    })
    return mock