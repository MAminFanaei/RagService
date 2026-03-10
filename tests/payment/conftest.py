# tests/payment/conftest.py
"""
Payment Service Test Fixtures
"""

import pytest
import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from typing import AsyncGenerator, Generator, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

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
# Session factory — fixture-scoped, bound to root async_engine
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def payment_session_factory(async_engine):
    """
    Build an async_sessionmaker bound to the shared test engine.
    Created per-test so it always points at the living engine.
    """
    return async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


# ─────────────────────────────────────────────────────────────
# Table setup — ONE fixture, no duplicates
# ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def setup_payment_tables(async_engine):
    """Create all tables before each test, drop after."""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ─────────────────────────────────────────────────────────────
# Mock Redis  (unchanged)
# ─────────────────────────────────────────────────────────────

class FakeRedis:
    """Minimal fake Redis for testing (no real Redis needed)."""

    def __init__(self):
        self._store: dict = {}
        self._lock: Optional[asyncio.Lock] = None

    def _get_lock(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def ping(self) -> bool:
        return True

    async def get(self, key: str):
        return self._store.get(key)

    async def set(self, key: str, value, ex: int = None, nx: bool = False):
        lock = self._get_lock()
        async with lock:
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
        lock = self._get_lock()
        async with lock:
            if numkeys == 1:
                key = args[0]
                expected_value = args[1] if len(args) > 1 else None
                stored = self._store.get(key)
                if stored == expected_value:
                    del self._store[key]
                    return 1
                return 0
            elif numkeys == 2:
                key = args[0]
                owners_key = args[1]
                owners = self._store.get(owners_key, {})
                if isinstance(owners, dict):
                    owner_value = owners.get(key)
                    current = self._store.get(key)
                    if owner_value and current == owner_value:
                        self._store.pop(key, None)
                        owners.pop(key, None)
                        return 1
                    owners.pop(key, None)
                return 0
            return 0

    async def incr(self, key: str) -> int:
        val = int(self._store.get(key, 0)) + 1
        self._store[key] = str(val)
        return val

    async def hset(self, name: str, key: str = None, value=None, mapping: dict = None):
        if name not in self._store or not isinstance(self._store[name], dict):
            self._store[name] = {}
        if key is not None:
            self._store[name][key] = value
        if mapping:
            self._store[name].update(mapping)
        return 1

    async def hget(self, name: str, key: str):
        data = self._store.get(name)
        if isinstance(data, dict):
            return data.get(key)
        return None

    async def hdel(self, name: str, *keys: str):
        data = self._store.get(name)
        if isinstance(data, dict):
            for key in keys:
                data.pop(key, None)
        return 1

    async def close(self):
        self._store.clear()

    async def aclose(self):
        self._store.clear()


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def override_redis(fake_redis):
    async def _get_fake_redis():
        return fake_redis
    app.dependency_overrides[get_redis] = _get_fake_redis
    yield fake_redis
    app.dependency_overrides.pop(get_redis, None)


# ─────────────────────────────────────────────────────────────
# Test User & Auth — use payment_session_factory, not module-level
# ─────────────────────────────────────────────────────────────

@pytest.fixture
async def test_user(setup_payment_tables, payment_session_factory) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username="testuser",
        email="test@example.com",
        hashed_password="hashed_fake_password",
        is_active=True,
        is_admin=False,
        is_verified=True,
    )
    async with payment_session_factory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


@pytest.fixture
async def admin_user(setup_payment_tables, payment_session_factory) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username="adminuser",
        email="admin@example.com",
        hashed_password="hashed_fake_password",
        is_active=True,
        is_admin=True,
        is_verified=True,
    )
    async with payment_session_factory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


@pytest.fixture
def auth_headers(test_user) -> dict:
    token = create_access_token(data={"sub": test_user.id, "type": "access"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_auth_headers(admin_user) -> dict:
    token = create_access_token(data={"sub": admin_user.id, "type": "access"})
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────────────────────
# Test HTTP Client — builds get_test_db from the fixture factory
# ─────────────────────────────────────────────────────────────

@pytest.fixture
async def client(
    override_redis,
    payment_session_factory,
) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP test client with test DB and fake Redis."""

    async def get_test_db() -> AsyncGenerator[AsyncSession, None]:
        async with payment_session_factory() as session:
            try:
                yield session
            finally:
                await session.close()

    app.dependency_overrides[get_db] = get_test_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.pop(get_db, None)


# ─────────────────────────────────────────────────────────────
# Mock SEP Gateway  (unchanged)
# ─────────────────────────────────────────────────────────────

class MockSEPGateway:
    # ... (keep exactly as-is — no engine involvement)
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
        self.calls.append({"method": "request_token", "kwargs": kwargs})
        from app.payment.services.sep_client import TokenResponse
        if self.should_fail_token:
            return TokenResponse(success=False, status=-1, token=None,
                                 error_code="5", error_desc="پارامترهای ارسال شده نامعتبر است")
        self.token_counter += 1
        return TokenResponse(success=True, status=1,
                             token=f"test_token_{self.token_counter:04d}",
                             error_code=None, error_desc=None)

    async def mock_verify_transaction(self, ref_num: str, **kwargs):
        self.calls.append({"method": "verify_transaction", "ref_num": ref_num, "kwargs": kwargs})
        if self.should_timeout:
            import httpx
            raise httpx.ReadTimeout("Simulated timeout")
        from app.payment.services.sep_client import VerifyResponse, VerifyTransactionDetail
        if self.should_fail_verify:
            return VerifyResponse(success=False, result_code=-2,
                                  result_description="تراکنش یافت نشد", transaction_detail=None)
        amount = self.verify_amount or 100000
        return VerifyResponse(
            success=True, result_code=self.verify_result_code,
            result_description="عملیات با موفقیت انجام شد",
            transaction_detail=VerifyTransactionDetail(
                rrn=f"RRN{uuid.uuid4().hex[:10]}", ref_num=ref_num,
                masked_pan="621986****8080",
                hashed_pan="b96a14400c3a59249e87c300ecc06e5920327e70220213b5bbb7d7b2410f7e0d",
                terminal_number=int(payment_settings.SEP_TERMINAL_ID),
                original_amount=amount, affective_amount=amount,
                strace_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                strace_no="100428",
            ),
        )

    async def mock_reverse_transaction(self, ref_num: str, **kwargs):
        self.calls.append({"method": "reverse_transaction", "ref_num": ref_num, "kwargs": kwargs})
        from app.payment.services.sep_client import VerifyResponse
        if self.should_fail_reverse:
            return VerifyResponse(success=False, result_code=-2,
                                  result_description="تراکنش یافت نشد", transaction_detail=None)
        return VerifyResponse(success=True, result_code=self.reverse_result_code,
                              result_description="موفق", transaction_detail=None)


@pytest.fixture
def mock_sep():
    return MockSEPGateway()


# ─────────────────────────────────────────────────────────────
# Data Factories — accept session as parameter (no module-level dependency)
# ─────────────────────────────────────────────────────────────

class PaymentFactory:
    @staticmethod
    async def create(session: AsyncSession, user_id: str, amount: int = 100000,
                     status: str = PaymentStatus.PENDING, ref_num: str = None,
                     res_num: str = None, token: str = None) -> Payment:
        payment = Payment(
            id=str(uuid.uuid4()), user_id=user_id,
            res_num=res_num or f"RES_{uuid.uuid4().hex[:12]}",
            ref_num=ref_num, amount=amount, original_amount=amount,
            discount_amount=0, terminal_id=str(payment_settings.SEP_TERMINAL_ID),
            token=token or f"tok_{uuid.uuid4().hex[:20]}",
            status=status,
            verified_at=datetime.now(timezone.utc) if status == PaymentStatus.VERIFIED else None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
        return payment


class WalletFactory:
    @staticmethod
    async def create(session: AsyncSession, user_id: str, balance: int = 0) -> Wallet:
        wallet = Wallet(
            id=str(uuid.uuid4()), user_id=user_id, balance=balance,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(wallet)
        await session.commit()
        await session.refresh(wallet)
        return wallet


class DiscountFactory:
    @staticmethod
    async def create(session: AsyncSession, code: str = "TEST20",
                     discount_type: str = DiscountType.PERCENTAGE,
                     discount_value: int = 20, max_discount: int = None,
                     min_purchase: int = 0, max_uses: int = None,
                     per_user_limit: int = 1, is_active: bool = True,
                     valid_from: datetime = None, valid_until: datetime = None) -> DiscountCode:
        dc = DiscountCode(
            id=str(uuid.uuid4()), code=code,
            discount_type=discount_type, discount_value=discount_value,
            max_discount=max_discount, min_purchase=min_purchase,
            max_uses=max_uses, used_count=0, per_user_limit=per_user_limit,
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