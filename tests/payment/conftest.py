"""
Payment Service Test Fixtures

Provides:
    - Async test database (SQLite in-memory for speed)
    - Test Redis (fakeredis)
    - Authenticated test client with JWT
    - Mock SEP gateway
    - Factory functions for sample data
"""

import pytest
import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db, get_redis
from app.core.security import create_access_token
from app.models.user import User
from app.main import app

from app.payment.models.payment import Payment
from app.payment.models.reverse import Reverse
from app.payment.models.wallet import Wallet
from app.payment.models.discount import DiscountCode
from app.payment.core.constants import (
    PaymentStatus,
    DiscountType,
)
from app.payment.config import payment_settings


# ─────────────────────────────────────────────────────────────
# Test Database (SQLite async in-memory)
# ─────────────────────────────────────────────────────────────

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    echo=False,
)

TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Create a single event loop for all tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
async def setup_database():
    """Create all tables before each test, drop after."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def get_test_db() -> AsyncGenerator[AsyncSession, None]:
    """Override get_db with test database session."""
    async with TestSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


# ─────────────────────────────────────────────────────────────
# Mock Redis
# ─────────────────────────────────────────────────────────────

class FakeRedis:
    """Minimal fake Redis for testing (no real Redis needed)."""

    def __init__(self):
        self._store: dict = {}
        self._locks: dict = {}

    async def ping(self) -> bool:
        return True

    async def get(self, key: str):
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: int = None, nx: bool = False):
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            if key in self._store:
                del self._store[key]
                count += 1
        return count

    async def exists(self, key: str) -> int:
        return 1 if key in self._store else 0

    async def eval(self, script: str, numkeys: int, *args):
        """Simplified Lua script eval for lock release."""
        if numkeys == 1:
            key = args[0]
            expected_value = args[1] if len(args) > 1 else None
            stored = self._store.get(key)
            if stored == expected_value:
                del self._store[key]
                return 1
        return 0

    async def incr(self, key: str) -> int:
        val = int(self._store.get(key, 0)) + 1
        self._store[key] = str(val)
        return val

    async def close(self):
        self._store.clear()

    async def aclose(self):
        self._store.clear()


@pytest.fixture
def fake_redis():
    """Provide a fresh FakeRedis instance."""
    return FakeRedis()


@pytest.fixture
def override_redis(fake_redis):
    """Override the Redis dependency with FakeRedis."""
    async def _get_fake_redis():
        return fake_redis
    app.dependency_overrides[get_redis] = _get_fake_redis
    yield fake_redis
    app.dependency_overrides.pop(get_redis, None)


# ─────────────────────────────────────────────────────────────
# Test User & Auth
# ─────────────────────────────────────────────────────────────

@pytest.fixture
async def test_user(setup_database) -> User:
    """Create a test user in the database."""
    user = User(
        id=str(uuid.uuid4()),
        username="testuser",
        email="test@example.com",
        hashed_password="hashed_fake_password",
        is_active=True,
        is_admin=False,
        is_verified=True,
    )
    async with TestSessionLocal() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


@pytest.fixture
async def admin_user(setup_database) -> User:
    """Create an admin test user."""
    user = User(
        id=str(uuid.uuid4()),
        username="adminuser",
        email="admin@example.com",
        hashed_password="hashed_fake_password",
        is_active=True,
        is_admin=True,
        is_verified=True,
    )
    async with TestSessionLocal() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


@pytest.fixture
def auth_headers(test_user) -> dict:
    """Get JWT auth headers for the test user."""
    token = create_access_token(data={"sub": test_user.id, "type": "access"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_auth_headers(admin_user) -> dict:
    """Get JWT auth headers for the admin user."""
    token = create_access_token(data={"sub": admin_user.id, "type": "access"})
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────────────────────
# Test HTTP Client
# ─────────────────────────────────────────────────────────────

@pytest.fixture
async def client(override_redis) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP test client with test DB and fake Redis."""
    app.dependency_overrides[get_db] = get_test_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.pop(get_db, None)


# ─────────────────────────────────────────────────────────────
# Mock SEP Gateway — FIXED: uses correct dataclass constructors
# ─────────────────────────────────────────────────────────────

class MockSEPGateway:
    """
    Simulates SEP API responses for testing.
    
    IMPORTANT: Uses snake_case field names matching the actual
    dataclass definitions in sep_client.py, NOT SEP's PascalCase.
    """

    def __init__(self):
        self.token_counter = 0
        self.should_fail_token = False
        self.should_fail_verify = False
        self.should_fail_reverse = False
        self.should_timeout = False
        self.verify_amount = None
        self.verify_result_code = 0
        self.reverse_result_code = 0
        self.calls: list[dict] = []

    def reset(self):
        self.token_counter = 0
        self.should_fail_token = False
        self.should_fail_verify = False
        self.should_fail_reverse = False
        self.should_timeout = False
        self.verify_amount = None
        self.verify_result_code = 0
        self.reverse_result_code = 0
        self.calls.clear()

    async def mock_request_token(self, **kwargs):
        """Simulate SEP Token API response."""
        self.calls.append({"method": "request_token", "kwargs": kwargs})

        from app.payment.services.sep_client import TokenResponse

        if self.should_fail_token:
            return TokenResponse(
                success=False,          # FIXED: was missing
                status=-1,
                token=None,
                error_code="5",
                error_desc="پارامترهای ارسال شده نامعتبر است",
            )

        self.token_counter += 1
        return TokenResponse(
            success=True,               # FIXED: was missing
            status=1,
            token=f"test_token_{self.token_counter:04d}",
            error_code=None,
            error_desc=None,
        )

    async def mock_verify_transaction(self, ref_num: str, **kwargs):
        """Simulate SEP VerifyTransaction API response."""
        self.calls.append({
            "method": "verify_transaction",
            "ref_num": ref_num,
            "kwargs": kwargs,
        })

        if self.should_timeout:
            import httpx
            raise httpx.ReadTimeout("Simulated timeout")

        from app.payment.services.sep_client import (
            VerifyResponse,
            VerifyTransactionDetail,
        )

        if self.should_fail_verify:
            return VerifyResponse(
                success=False,                          # FIXED: was Success
                result_code=-2,                         # FIXED: was ResultCode
                result_description="تراکنش یافت نشد",  # FIXED: was ResultDescription
                transaction_detail=None,                # FIXED: was TransactionDetail
            )

        amount = self.verify_amount or 100000

        return VerifyResponse(
            success=True,                               # FIXED: was Success
            result_code=self.verify_result_code,        # FIXED: was ResultCode
            result_description="عملیات با موفقیت انجام شد",
            transaction_detail=VerifyTransactionDetail( # FIXED: was TransactionDetail
                rrn=f"RRN{uuid.uuid4().hex[:10]}",      # FIXED: was RRN
                ref_num=ref_num,                        # FIXED: was RefNum
                masked_pan="621986****8080",             # FIXED: was MaskedPan
                hashed_pan="b96a14400c3a59249e87c300ecc06e5920327e70220213b5bbb7d7b2410f7e0d",
                terminal_number=int(payment_settings.SEP_TERMINAL_ID),
                original_amount=amount,                 # FIXED: was OrginalAmount
                affective_amount=amount,                # FIXED: was AffectiveAmount
                strace_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                strace_no="100428",                     # FIXED: was StraceNo
            ),
        )

    async def mock_reverse_transaction(self, ref_num: str, **kwargs):
        """Simulate SEP ReverseTransaction API response."""
        self.calls.append({
            "method": "reverse_transaction",
            "ref_num": ref_num,
            "kwargs": kwargs,
        })

        from app.payment.services.sep_client import VerifyResponse

        if self.should_fail_reverse:
            return VerifyResponse(
                success=False,                  # FIXED
                result_code=-2,                 # FIXED
                result_description="تراکنش یافت نشد",
                transaction_detail=None,        # FIXED
            )

        return VerifyResponse(
            success=True,                       # FIXED
            result_code=self.reverse_result_code,
            result_description="موفق",
            transaction_detail=None,            # FIXED
        )


@pytest.fixture
def mock_sep():
    """Provide a configurable mock SEP gateway."""
    return MockSEPGateway()


# ─────────────────────────────────────────────────────────────
# Data Factories
# ─────────────────────────────────────────────────────────────

class PaymentFactory:
    """Create sample payment records for testing."""

    @staticmethod
    async def create(
        session: AsyncSession,
        user_id: str,
        amount: int = 100000,
        status: str = PaymentStatus.PENDING,
        ref_num: str = None,
        res_num: str = None,
        token: str = None,
    ) -> Payment:
        payment = Payment(
            id=str(uuid.uuid4()),
            user_id=user_id,
            res_num=res_num or f"RES_{uuid.uuid4().hex[:12]}",
            ref_num=ref_num,
            amount=amount,
            original_amount=amount,
            discount_amount=0,
            terminal_id=str(payment_settings.SEP_TERMINAL_ID),
            token=token or f"tok_{uuid.uuid4().hex[:20]}",
            status=status,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
        return payment


class WalletFactory:
    """Create sample wallets for testing."""

    @staticmethod
    async def create(
        session: AsyncSession,
        user_id: str,
        balance: int = 0,
    ) -> Wallet:
        wallet = Wallet(
            id=str(uuid.uuid4()),
            user_id=user_id,
            balance=balance,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(wallet)
        await session.commit()
        await session.refresh(wallet)
        return wallet


class DiscountFactory:
    """Create sample discount codes for testing."""

    @staticmethod
    async def create(
        session: AsyncSession,
        code: str = "TEST20",
        discount_type: str = DiscountType.PERCENTAGE,
        discount_value: int = 20,
        max_discount: int = None,
        min_purchase: int = 0,
        max_uses: int = None,
        per_user_limit: int = 1,
        is_active: bool = True,
        valid_from: datetime = None,
        valid_until: datetime = None,
    ) -> DiscountCode:
        dc = DiscountCode(
            id=str(uuid.uuid4()),
            code=code,
            discount_type=discount_type,
            discount_value=discount_value,
            max_discount=max_discount,
            min_purchase=min_purchase,
            max_uses=max_uses,
            used_count=0,
            per_user_limit=per_user_limit,
            is_active=is_active,
            valid_from=valid_from or datetime.now(timezone.utc) - timedelta(days=1),
            valid_until=valid_until or datetime.now(timezone.utc) + timedelta(days=30),
            created_at=datetime.now(timezone.utc),
        )
        session.add(dc)
        await session.commit()
        await session.refresh(dc)
        return dc


@pytest.fixture
def payment_factory():
    return PaymentFactory()


@pytest.fixture
def wallet_factory():
    return WalletFactory()


@pytest.fixture
def discount_factory():
    return DiscountFactory()
