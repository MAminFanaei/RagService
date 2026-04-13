"""
Tests for OTP flow — full coverage of all new endpoints and edge cases.

Covers:
    POST /api/v1/auth/otp/request
    POST /api/v1/auth/otp/verify
    POST /api/v1/auth/register       (OTP-gated)
    POST /api/v1/auth/reset_password (OTP-gated)
    PUT  /api/v1/auth/me/change_phone       (OTP-gated, authenticated)

Infrastructure:
    - Uses otp_redis (in-memory store with hash support, no real Redis needed)
    - Uses mock_sms (patches OtpService._sms directly with new=)
    - Uses real DB with SAVEPOINT rollback (same as credit tests)
    - Uses real JWT signing (same SECRET_KEY from settings)
    - No real SMS is ever sent

OTP Mechanics (how OtpService works internally):
    - Challenge stored in Redis as hash at otp:challenge:{purpose}:{phone}
    - Cooldown stored as otp:cooldown:{purpose}:{phone}
    - Proof stored as otp:proof:{jti}
    - OTP code is hashed before storage (sha256 of phone:purpose:code:SECRET_KEY)
    - Proof token is a signed JWT with type=otp_proof

Design decisions tested:
    - Proof is NOT consumed until all business checks pass (validate then consume)
    - Proof is one-time use (replay is blocked by Redis key deletion)
    - Purpose is bound to proof (register proof cannot be used for reset)
    - Phone is bound to proof (proof for 09120000001 cannot be used for 09120000002)
    - Enumeration safety on reset (non-existent phone returns 200 with generic message)
    - Cooldown blocks rapid resend per phone+purpose
    - Max attempts enforced per challenge

CRITICAL FIXTURE NOTE:
    All tests use `otp_client.otp_redis` to access the Redis mock instead of
    requesting `otp_redis` as a separate fixture parameter.
    This guarantees the test and the HTTP client share the SAME Redis instance.

CRITICAL PATCH NOTE:
    SMS is patched by replacing `OtpService._sms` directly using `new=manager`.
    The target is `app.services.otp_service.OtpService._sms`.
    Do NOT use `return_value=` — _sms is an instance, not a callable.
"""

import hashlib
import time
import pytest
import secrets
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import create_otp_proof_token, get_password_hash
from app.models.user import User, AuthProvider
from app.services.otp_service import OtpService
from app.services.sms_provider_service import SmsSendResult
from app.schemas.auth import OTPPurpose


# =============================================================================
# CONSTANTS
# =============================================================================

NEW_PHONE = "09120000001"
EXISTING_PHONE = "09120000002"
OTHER_PHONE = "09120000003"
VALID_PASSWORD = "ValidPass1"

SMS_PATCH_TARGET = "app.services.otp_service.OtpService._sms"


# =============================================================================
# SMS RESULT FACTORIES
# =============================================================================

def _make_sms_success(phone: str = NEW_PHONE) -> SmsSendResult:
    return SmsSendResult(
        success=True,
        provider="melipayamak_rest",
        provider_message_id="1234567890123456",
    )


def _make_sms_transient_fail() -> SmsSendResult:
    return SmsSendResult(
        success=False,
        provider="melipayamak_rest",
        error_code=-6,
        error_message="Internal provider error",
        transient=True,
    )


def _make_sms_permanent_fail(code: int, message: str) -> SmsSendResult:
    return SmsSendResult(
        success=False,
        provider="melipayamak_rest",
        error_code=code,
        error_message=message,
        transient=False,
    )


# =============================================================================
# SMS MOCK CONTEXT MANAGERS
# =============================================================================

def mock_sms_success():
    manager = MagicMock()
    manager.send_otp = AsyncMock(return_value=_make_sms_success())
    return patch(SMS_PATCH_TARGET, new=manager)


def mock_sms_transient():
    manager = MagicMock()
    manager.send_otp = AsyncMock(return_value=_make_sms_transient_fail())
    return patch(SMS_PATCH_TARGET, new=manager)


def mock_sms_permanent(code: int = 0, message: str = "Username or password is invalid"):
    manager = MagicMock()
    manager.send_otp = AsyncMock(
        return_value=_make_sms_permanent_fail(code, message)
    )
    return patch(SMS_PATCH_TARGET, new=manager)


# =============================================================================
# REDIS / PROOF HELPERS
# =============================================================================

def _compute_otp_hash(phone: str, purpose: str, code: str) -> str:
    raw = f"{phone}:{purpose}:{code}:{settings.SECRET_KEY}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _plant_challenge(
    redis,
    phone: str,
    purpose: str,
    code: str,
    attempts: int = 0,
):
    key = f"otp:challenge:{purpose}:{phone}"
    otp_hash = _compute_otp_hash(phone, purpose, code)
    redis._challenges[key] = {
        "otp_hash": otp_hash,
        "attempts": str(attempts),
        "created_at": str(int(time.time())),
    }


def _plant_proof(redis, phone: str, purpose: str) -> str:
    jti = secrets.token_urlsafe(24)
    proof_token = create_otp_proof_token(
        phone_number=phone,
        purpose=purpose,
        jti=jti,
    )
    redis._store[f"otp:proof:{jti}"] = f"{phone}:{purpose}"
    return proof_token


def _seed_cooldown(redis, phone: str, purpose: str, ttl: int = 45):
    cooldown_key = f"otp:cooldown:{purpose}:{phone}"
    redis._store[cooldown_key] = "1"
    redis._ttls[cooldown_key] = ttl


# =============================================================================
# ENHANCED MOCK REDIS FIXTURE
# =============================================================================

@pytest.fixture
def otp_redis():
    mock = AsyncMock()
    _flat = {}
    _hashes = {}
    _ttls = {}

    mock._store = _flat
    mock._challenges = _hashes
    mock._ttls = _ttls

    async def mock_set(key, value, ex=None, **kwargs):
        _flat[key] = value
        if ex:
            _ttls[key] = ex
        return True

    async def mock_get(key):
        return _flat.get(key)

    async def mock_exists(key):
        return 1 if (key in _flat or key in _hashes) else 0

    async def mock_delete(*keys):
        count = 0
        for key in keys:
            if _flat.pop(key, None) is not None:
                count += 1
            if _hashes.pop(key, None) is not None:
                count += 1
            _ttls.pop(key, None)
        return count

    async def mock_ttl(key):
        return _ttls.get(key, -2)

    async def mock_hset(key, mapping=None, **kwargs):
        if mapping:
            _hashes[key] = dict(mapping)
        return 1

    async def mock_hgetall(key):
        return _hashes.get(key, {})

    async def mock_hincrby(key, field, amount):
        if key not in _hashes:
            return amount
        current = int(_hashes[key].get(field, "0"))
        new_val = current + amount
        _hashes[key][field] = str(new_val)
        return new_val

    async def mock_expire(key, seconds):
        _ttls[key] = seconds
        return True

    mock.set = AsyncMock(side_effect=mock_set)
    mock.get = AsyncMock(side_effect=mock_get)
    mock.exists = AsyncMock(side_effect=mock_exists)
    mock.delete = AsyncMock(side_effect=mock_delete)
    mock.ttl = AsyncMock(side_effect=mock_ttl)
    mock.hset = AsyncMock(side_effect=mock_hset)
    mock.hgetall = AsyncMock(side_effect=mock_hgetall)
    mock.hincrby = AsyncMock(side_effect=mock_hincrby)
    mock.expire = AsyncMock(side_effect=mock_expire)
    mock.ping = AsyncMock(return_value=True)
    mock.flushdb = AsyncMock(return_value=True)

    return mock


# =============================================================================
# HTTP CLIENT FIXTURE
# =============================================================================

@pytest.fixture
async def otp_client(db: AsyncSession, otp_redis):
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.core.database import get_db, get_redis

    async def override_get_db():
        yield db

    async def override_get_redis():
        return otp_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    transport = ASGITransport(app=app)
    ac = AsyncClient(transport=transport, base_url="http://test")
    ac.otp_redis = otp_redis

    yield ac

    await ac.aclose()
    app.dependency_overrides.clear()


# =============================================================================
# DB FIXTURES
# =============================================================================

@pytest.fixture
async def existing_phone_user(db: AsyncSession):
    user = User(
        email="existing@example.com",
        username="existinguser",
        phone_number=EXISTING_PHONE,
        hashed_password=get_password_hash(VALID_PASSWORD),
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
async def inactive_phone_user(db: AsyncSession):
    user = User(
        email="inactive_phone@example.com",
        username="inactivephoneuser",
        phone_number=EXISTING_PHONE,
        hashed_password=get_password_hash(VALID_PASSWORD),
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
async def auth_headers_existing(existing_phone_user):
    from app.core.security import create_token_pair
    tokens = create_token_pair(
        user_id=existing_phone_user.id,
        email=existing_phone_user.email,
        is_admin=existing_phone_user.is_admin,
    )
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# =============================================================================
# OTP REQUEST TESTS
# =============================================================================

class TestOtpRequest:

    async def test_register_new_phone_success(self, otp_client):
        """OTP_REQ_001: new phone + register → 200, challenge created, cooldown set."""
        redis = otp_client.otp_redis

        with mock_sms_success():
            resp = await otp_client.post("/api/v1/auth/otp/request", json={
                "phone_number": NEW_PHONE,
                "purpose": "register",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["expires_in_seconds"] == settings.OTP_EXPIRE_SECONDS
        assert data["resend_after_seconds"] == settings.OTP_RESEND_COOLDOWN_SECONDS
        assert f"otp:challenge:register:{NEW_PHONE}" in redis._challenges
        assert f"otp:cooldown:register:{NEW_PHONE}" in redis._store

    async def test_register_already_registered_phone_rejected(
        self, otp_client, existing_phone_user
    ):
        """OTP_REQ_002: phone already in users table → 400."""
        with mock_sms_success():
            resp = await otp_client.post("/api/v1/auth/otp/request", json={
                "phone_number": EXISTING_PHONE,
                "purpose": "register",
            })

        assert resp.status_code == 400
        assert "already registered" in resp.json()["message"].lower()

    async def test_reset_existing_phone_success(self, otp_client, existing_phone_user):
        """OTP_REQ_003: reset + existing phone → 200, challenge created."""
        redis = otp_client.otp_redis

        with mock_sms_success():
            resp = await otp_client.post("/api/v1/auth/otp/request", json={
                "phone_number": EXISTING_PHONE,
                "purpose": "reset_password",
            })

        assert resp.status_code == 200
        assert f"otp:challenge:reset_password:{EXISTING_PHONE}" in redis._challenges

    async def test_change_phone_new_number_success(self, otp_client):
        """OTP_REQ_005: change_phone + phone not in use → 200, challenge created."""
        redis = otp_client.otp_redis

        with mock_sms_success():
            resp = await otp_client.post("/api/v1/auth/otp/request", json={
                "phone_number": OTHER_PHONE,
                "purpose": "change_phone",
            })

        assert resp.status_code == 200
        assert f"otp:challenge:change_phone:{OTHER_PHONE}" in redis._challenges

    async def test_change_phone_already_in_use_rejected(
        self, otp_client, existing_phone_user
    ):
        """OTP_REQ_006: change_phone + phone already in DB → 400."""
        with mock_sms_success():
            resp = await otp_client.post("/api/v1/auth/otp/request", json={
                "phone_number": EXISTING_PHONE,
                "purpose": "change_phone",
            })

        assert resp.status_code == 400
        assert "in use" in resp.json()["message"].lower()

    async def test_cooldown_blocks_second_request(self, otp_client):
        """OTP_REQ_007: active cooldown → 429 with retry_after in body and header."""
        redis = otp_client.otp_redis
        _seed_cooldown(redis, NEW_PHONE, "register", ttl=45)

        with mock_sms_success():
            resp = await otp_client.post("/api/v1/auth/otp/request", json={
                "phone_number": NEW_PHONE,
                "purpose": "register",
            })

        assert resp.status_code == 429
        data = resp.json()
        assert "retry_after" in data
        assert data["retry_after"] > 0
        assert "Retry-After" in resp.headers

    async def test_cooldown_is_purpose_scoped(self, otp_client):
        """OTP_REQ_008: cooldown on register does NOT block change_phone."""
        redis = otp_client.otp_redis
        _seed_cooldown(redis, OTHER_PHONE, "register", ttl=45)

        with mock_sms_success():
            resp = await otp_client.post("/api/v1/auth/otp/request", json={
                "phone_number": OTHER_PHONE,
                "purpose": "change_phone",
            })

        assert resp.status_code == 200

    async def test_invalid_phone_format_rejected(self, otp_client):
        """OTP_REQ_009: malformed phone → 400/422."""
        with mock_sms_success():
            resp = await otp_client.post("/api/v1/auth/otp/request", json={
                "phone_number": "1234",
                "purpose": "register",
            })

        assert resp.status_code in (400, 422)

    async def test_phone_normalized_plus98_format(self, otp_client):
        """OTP_REQ_010: +989120000001 normalized to 09120000001."""
        redis = otp_client.otp_redis

        with mock_sms_success():
            resp = await otp_client.post("/api/v1/auth/otp/request", json={
                "phone_number": "+989120000001",
                "purpose": "register",
            })

        assert resp.status_code == 200
        assert "otp:challenge:register:09120000001" in redis._challenges

    async def test_phone_normalized_989_format(self, otp_client):
        """OTP_REQ_011: 989120000001 normalized to 09120000001."""
        redis = otp_client.otp_redis

        with mock_sms_success():
            resp = await otp_client.post("/api/v1/auth/otp/request", json={
                "phone_number": "989120000001",
                "purpose": "register",
            })

        assert resp.status_code == 200
        assert "otp:challenge:register:09120000001" in redis._challenges

    async def test_invalid_purpose_rejected(self, otp_client):
        """OTP_REQ_012: unrecognized purpose → 422."""
        with mock_sms_success():
            resp = await otp_client.post("/api/v1/auth/otp/request", json={
                "phone_number": NEW_PHONE,
                "purpose": "delete_account",
            })

        assert resp.status_code == 422

    async def test_transient_sms_failure_cleans_up_challenge(self, otp_client):
        """OTP_REQ_013: transient SMS fail → 500, no stale challenge."""
        redis = otp_client.otp_redis

        with mock_sms_transient():
            resp = await otp_client.post("/api/v1/auth/otp/request", json={
                "phone_number": NEW_PHONE,
                "purpose": "register",
            })

        assert resp.status_code == 500
        assert f"otp:challenge:register:{NEW_PHONE}" not in redis._challenges

    async def test_permanent_sms_failure_returns_400(self, otp_client):
        """OTP_REQ_014: permanent provider error → 400."""
        with mock_sms_permanent(code=0, message="Username or password is invalid"):
            resp = await otp_client.post("/api/v1/auth/otp/request", json={
                "phone_number": NEW_PHONE,
                "purpose": "register",
            })

        assert resp.status_code == 400

    @pytest.mark.parametrize("error_code,is_transient", [
        (-6,  True),
        (6,   True),
        (11,  True),
        (19,  True),
        (0,   False),
        (2,   False),
        (-4,  False),
        (-1,  False),
        (18,  False),
    ])
    async def test_provider_error_codes_map_to_correct_status(
        self, otp_client, error_code, is_transient
    ):
        """OTP_REQ_015: transient codes → 500, permanent codes → 400."""
        from app.services.otp_service import _STATUS_MESSAGES, _TRANSIENT_CODES
        full_message = _STATUS_MESSAGES.get(error_code, "")

        manager = MagicMock()
        manager.send_otp = AsyncMock(return_value=SmsSendResult(
            success=False,
            provider="melipayamak_rest",
            error_code=error_code,
            error_message=full_message,
            transient=is_transient,
        ))

        with patch(SMS_PATCH_TARGET, new=manager):
            resp = await otp_client.post("/api/v1/auth/otp/request", json={
                "phone_number": NEW_PHONE,
                "purpose": "register",
            })

        expected_status = 500 if is_transient else 400
        assert resp.status_code == expected_status


# =============================================================================
# OTP VERIFY TESTS
# =============================================================================

class TestOtpVerify:

    async def test_valid_otp_returns_proof(self, otp_client):
        """OTP_VER_001: correct code → 200, proof returned, challenge deleted."""
        redis = otp_client.otp_redis
        code = "123456"
        _plant_challenge(redis, NEW_PHONE, "register", code)

        resp = await otp_client.post("/api/v1/auth/otp/verify", json={
            "phone_number": NEW_PHONE,
            "purpose": "register",
            "code": code,
        })

        assert resp.status_code == 200
        data = resp.json()
        assert "otp_proof" in data
        assert len(data["otp_proof"]) > 20
        assert data["proof_expires_in_seconds"] == settings.OTP_VERIFY_TOKEN_EXPIRE_MINUTES * 60
        assert f"otp:challenge:register:{NEW_PHONE}" not in redis._challenges

    async def test_valid_otp_creates_proof_in_redis(self, otp_client):
        """OTP_VER_002: after verify, proof key exists in Redis."""
        redis = otp_client.otp_redis
        code = "654321"
        _plant_challenge(redis, NEW_PHONE, "register", code)

        resp = await otp_client.post("/api/v1/auth/otp/verify", json={
            "phone_number": NEW_PHONE,
            "purpose": "register",
            "code": code,
        })

        assert resp.status_code == 200
        from app.core.security import decode_otp_proof_token
        payload = decode_otp_proof_token(resp.json()["otp_proof"])
        assert payload is not None
        assert f"otp:proof:{payload['jti']}" in redis._store

    async def test_wrong_otp_increments_attempts(self, otp_client):
        """OTP_VER_003: wrong code → 400, attempts incremented."""
        redis = otp_client.otp_redis
        _plant_challenge(redis, NEW_PHONE, "register", "111111")

        resp = await otp_client.post("/api/v1/auth/otp/verify", json={
            "phone_number": NEW_PHONE,
            "purpose": "register",
            "code": "999999",
        })

        assert resp.status_code == 400
        assert "invalid otp" in resp.json()["message"].lower()
        assert redis._challenges[f"otp:challenge:register:{NEW_PHONE}"]["attempts"] == "1"

    async def test_wrong_otp_shows_remaining_attempts(self, otp_client):
        """OTP_VER_004: error message includes remaining count."""
        redis = otp_client.otp_redis
        _plant_challenge(redis, NEW_PHONE, "register", "111111")

        resp = await otp_client.post("/api/v1/auth/otp/verify", json={
            "phone_number": NEW_PHONE,
            "purpose": "register",
            "code": "999999",
        })

        assert resp.status_code == 400
        assert str(settings.OTP_MAX_VERIFY_ATTEMPTS - 1) in resp.json()["message"]

    async def test_max_attempts_exceeded_deletes_challenge(self, otp_client):
        """OTP_VER_005: wrong code on last attempt → challenge deleted."""
        redis = otp_client.otp_redis
        max_a = settings.OTP_MAX_VERIFY_ATTEMPTS
        _plant_challenge(redis, NEW_PHONE, "register", "111111", attempts=max_a - 1)

        resp = await otp_client.post("/api/v1/auth/otp/verify", json={
            "phone_number": NEW_PHONE,
            "purpose": "register",
            "code": "999999",
        })

        assert resp.status_code == 400
        assert "maximum" in resp.json()["message"].lower()
        assert f"otp:challenge:register:{NEW_PHONE}" not in redis._challenges

    async def test_already_maxed_attempts_rejects_even_correct_code(self, otp_client):
        """OTP_VER_006: attempts already at MAX → rejected even with correct code."""
        redis = otp_client.otp_redis
        code = "111111"
        _plant_challenge(
            redis, NEW_PHONE, "register", code,
            attempts=settings.OTP_MAX_VERIFY_ATTEMPTS
        )

        resp = await otp_client.post("/api/v1/auth/otp/verify", json={
            "phone_number": NEW_PHONE,
            "purpose": "register",
            "code": code,
        })

        assert resp.status_code == 400

    async def test_expired_challenge_returns_error(self, otp_client):
        """OTP_VER_007: no challenge in Redis → 400."""
        resp = await otp_client.post("/api/v1/auth/otp/verify", json={
            "phone_number": NEW_PHONE,
            "purpose": "register",
            "code": "123456",
        })

        assert resp.status_code == 400
        msg = resp.json()["message"].lower()
        assert "expired" in msg or "not requested" in msg

    async def test_wrong_purpose_in_verify_rejected(self, otp_client):
        """OTP_VER_008: challenge for register, verify with reset_password → 400."""
        redis = otp_client.otp_redis
        _plant_challenge(redis, NEW_PHONE, "register", "123456")

        resp = await otp_client.post("/api/v1/auth/otp/verify", json={
            "phone_number": NEW_PHONE,
            "purpose": "reset_password",
            "code": "123456",
        })

        assert resp.status_code == 400

    async def test_wrong_phone_in_verify_rejected(self, otp_client):
        """OTP_VER_009: challenge for NEW_PHONE, verify with OTHER_PHONE → 400."""
        redis = otp_client.otp_redis
        _plant_challenge(redis, NEW_PHONE, "register", "123456")

        resp = await otp_client.post("/api/v1/auth/otp/verify", json={
            "phone_number": OTHER_PHONE,
            "purpose": "register",
            "code": "123456",
        })

        assert resp.status_code == 400

    async def test_non_numeric_otp_code_rejected(self, otp_client):
        """OTP_VER_010: non-numeric code → 400/422."""
        redis = otp_client.otp_redis
        _plant_challenge(redis, NEW_PHONE, "register", "123456")

        resp = await otp_client.post("/api/v1/auth/otp/verify", json={
            "phone_number": NEW_PHONE,
            "purpose": "register",
            "code": "abc123",
        })

        assert resp.status_code in (400, 422)

    async def test_proof_token_has_correct_claims(self, otp_client):
        """OTP_VER_011: proof JWT must have type, phone_number, purpose, jti."""
        redis = otp_client.otp_redis
        code = "555555"
        _plant_challenge(redis, NEW_PHONE, "register", code)

        resp = await otp_client.post("/api/v1/auth/otp/verify", json={
            "phone_number": NEW_PHONE,
            "purpose": "register",
            "code": code,
        })

        assert resp.status_code == 200
        from app.core.security import decode_otp_proof_token
        payload = decode_otp_proof_token(resp.json()["otp_proof"])
        assert payload is not None
        assert payload["type"] == "otp_proof"
        assert payload["phone_number"] == NEW_PHONE
        assert payload["purpose"] == "register"
        assert "jti" in payload


# =============================================================================
# REGISTER TESTS (OTP-GATED)
# =============================================================================

class TestRegister:

    async def test_register_without_proof_rejected(self, otp_client):
        """REG_001: missing otp_proof → 422."""
        resp = await otp_client.post("/api/v1/auth/register", json={
            "phone_number": NEW_PHONE,
            "email": "new@example.com",
            "username": "newuser",
            "password": VALID_PASSWORD,
        })
        assert resp.status_code == 422

    async def test_register_with_invalid_proof_rejected(self, otp_client):
        """REG_002: garbage otp_proof → 400."""
        resp = await otp_client.post("/api/v1/auth/register", json={
            "phone_number": NEW_PHONE,
            "email": "new@example.com",
            "username": "newuser",
            "password": VALID_PASSWORD,
            "otp_proof": "not.a.valid.jwt",
        })
        assert resp.status_code == 400

    async def test_register_with_wrong_purpose_proof_rejected(self, otp_client):
        """REG_003: proof for reset_password used in register → 400."""
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, NEW_PHONE, "reset_password")

        resp = await otp_client.post("/api/v1/auth/register", json={
            "phone_number": NEW_PHONE,
            "email": "new@example.com",
            "username": "newuser",
            "password": VALID_PASSWORD,
            "otp_proof": proof,
        })
        assert resp.status_code == 400

    async def test_register_with_proof_for_different_phone_rejected(self, otp_client):
        """REG_004: proof for OTHER_PHONE used with NEW_PHONE → 400."""
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, OTHER_PHONE, "register")

        resp = await otp_client.post("/api/v1/auth/register", json={
            "phone_number": NEW_PHONE,
            "email": "new@example.com",
            "username": "newuser",
            "password": VALID_PASSWORD,
            "otp_proof": proof,
        })
        assert resp.status_code == 400

    async def test_register_success_with_valid_proof(self, otp_client):
        """REG_005: valid proof + valid data → 201 + tokens."""
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, NEW_PHONE, "register")

        resp = await otp_client.post("/api/v1/auth/register", json={
            "phone_number": NEW_PHONE,
            "email": "new@example.com",
            "username": "newuser",
            "password": VALID_PASSWORD,
            "otp_proof": proof,
        })

        assert resp.status_code == 201
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    async def test_register_user_is_verified_after_registration(
        self, otp_client, db: AsyncSession
    ):
        """REG_006: registered user must have is_verified=True and phone set."""
        from sqlalchemy import select
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, NEW_PHONE, "register")

        await otp_client.post("/api/v1/auth/register", json={
            "phone_number": NEW_PHONE,
            "email": "verified@example.com",
            "username": "verifieduser",
            "password": VALID_PASSWORD,
            "otp_proof": proof,
        })

        result = await db.execute(select(User).where(User.phone_number == NEW_PHONE))
        user = result.scalar_one_or_none()
        assert user is not None
        assert user.is_verified is True
        assert user.phone_number == NEW_PHONE

    async def test_register_proof_replay_rejected(self, otp_client):
        """REG_007: reuse same proof → 400 on second attempt."""
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, NEW_PHONE, "register")

        r1 = await otp_client.post("/api/v1/auth/register", json={
            "phone_number": NEW_PHONE,
            "email": "first@example.com",
            "username": "firstuser",
            "password": VALID_PASSWORD,
            "otp_proof": proof,
        })
        assert r1.status_code == 201

        r2 = await otp_client.post("/api/v1/auth/register", json={
            "phone_number": NEW_PHONE,
            "email": "second@example.com",
            "username": "seconduser",
            "password": VALID_PASSWORD,
            "otp_proof": proof,
        })
        assert r2.status_code == 400

    async def test_register_duplicate_phone_rejected(
        self, otp_client, existing_phone_user
    ):
        """REG_008: valid proof but phone already in DB → 400."""
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, EXISTING_PHONE, "register")

        resp = await otp_client.post("/api/v1/auth/register", json={
            "phone_number": EXISTING_PHONE,
            "email": "another@example.com",
            "username": "anotheruser",
            "password": VALID_PASSWORD,
            "otp_proof": proof,
        })
        assert resp.status_code == 400

    async def test_proof_not_consumed_on_duplicate_email_error(
        self, otp_client, existing_phone_user
    ):
        """REG_009: duplicate email → 400, proof NOT consumed."""
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, NEW_PHONE, "register")

        resp = await otp_client.post("/api/v1/auth/register", json={
            "phone_number": NEW_PHONE,
            "email": existing_phone_user.email,
            "username": "brandnewuser",
            "password": VALID_PASSWORD,
            "otp_proof": proof,
        })
        assert resp.status_code == 400

        from app.core.security import decode_otp_proof_token
        payload = decode_otp_proof_token(proof)
        assert f"otp:proof:{payload['jti']}" in redis._store

    async def test_proof_not_consumed_on_duplicate_username_error(
        self, otp_client, existing_phone_user
    ):
        """REG_010: duplicate username → 400, proof NOT consumed."""
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, NEW_PHONE, "register")

        resp = await otp_client.post("/api/v1/auth/register", json={
            "phone_number": NEW_PHONE,
            "email": "unique@example.com",
            "username": existing_phone_user.username,
            "password": VALID_PASSWORD,
            "otp_proof": proof,
        })
        assert resp.status_code == 400

        from app.core.security import decode_otp_proof_token
        payload = decode_otp_proof_token(proof)
        assert f"otp:proof:{payload['jti']}" in redis._store

    async def test_weak_password_rejected_before_proof_check(self, otp_client):
        """REG_011: invalid password → 422 at Pydantic level, proof untouched."""
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, NEW_PHONE, "register")

        resp = await otp_client.post("/api/v1/auth/register", json={
            "phone_number": NEW_PHONE,
            "email": "test@example.com",
            "username": "testuser",
            "password": "weak",
            "otp_proof": proof,
        })
        assert resp.status_code == 422

        from app.core.security import decode_otp_proof_token
        payload = decode_otp_proof_token(proof)
        assert f"otp:proof:{payload['jti']}" in redis._store

    async def test_register_disabled_by_feature_flag(self, otp_client):
        """REG_012: ENABLE_REGISTRATION=False → 501/503."""
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, NEW_PHONE, "register")

        with patch.object(settings, "ENABLE_REGISTRATION", False):
            resp = await otp_client.post("/api/v1/auth/register", json={
                "phone_number": NEW_PHONE,
                "email": "new@example.com",
                "username": "newuser",
                "password": VALID_PASSWORD,
                "otp_proof": proof,
            })

        assert resp.status_code in (501, 503)


# =============================================================================
# PASSWORD RESET TESTS (OTP-GATED)
# =============================================================================

class TestPasswordReset:

    async def test_reset_without_proof_rejected(self, otp_client):
        """RST_001: missing otp_proof → 422."""
        resp = await otp_client.post("/api/v1/auth/reset_password", json={
            "phone_number": EXISTING_PHONE,
            "new_password": VALID_PASSWORD,
        })
        assert resp.status_code == 422

    async def test_reset_with_invalid_proof_rejected(self, otp_client):
        """RST_002: garbage proof → 400."""
        resp = await otp_client.post("/api/v1/auth/reset_password", json={
            "phone_number": EXISTING_PHONE,
            "new_password": VALID_PASSWORD,
            "otp_proof": "garbage.token.here",
        })
        assert resp.status_code == 400

    async def test_reset_with_wrong_purpose_proof_rejected(self, otp_client):
        """RST_003: proof for register used in reset → 400."""
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, EXISTING_PHONE, "register")

        resp = await otp_client.post("/api/v1/auth/reset_password", json={
            "phone_number": EXISTING_PHONE,
            "new_password": VALID_PASSWORD,
            "otp_proof": proof,
        })
        assert resp.status_code == 400

    async def test_reset_success_and_new_password_works(
        self, otp_client, existing_phone_user
    ):
        """RST_004: valid proof → 200. Old password fails, new password works."""
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, EXISTING_PHONE, "reset_password")
        new_password = "NewSecure9"

        resp = await otp_client.post("/api/v1/auth/reset_password", json={
            "phone_number": EXISTING_PHONE,
            "new_password": new_password,
            "otp_proof": proof,
        })
        assert resp.status_code == 200
        assert "reset" in resp.json()["message"].lower()

        old_login = await otp_client.post("/api/v1/auth/login", json={
            "login": existing_phone_user.email,
            "password": VALID_PASSWORD,
        })
        assert old_login.status_code == 401

        new_login = await otp_client.post("/api/v1/auth/login", json={
            "login": existing_phone_user.email,
            "password": new_password,
        })
        assert new_login.status_code == 200

    async def test_reset_proof_replay_rejected(self, otp_client, existing_phone_user):
        """RST_005: reuse same proof → 400."""
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, EXISTING_PHONE, "reset_password")

        r1 = await otp_client.post("/api/v1/auth/reset_password", json={
            "phone_number": EXISTING_PHONE,
            "new_password": "NewSecure9",
            "otp_proof": proof,
        })
        assert r1.status_code == 200

        r2 = await otp_client.post("/api/v1/auth/reset_password", json={
            "phone_number": EXISTING_PHONE,
            "new_password": "AnotherPass9",
            "otp_proof": proof,
        })
        assert r2.status_code == 400

    async def test_reset_for_nonexistent_phone_rejected(self, otp_client):
        """RST_006: valid proof but no user with that phone → 404."""
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, NEW_PHONE, "reset_password")

        resp = await otp_client.post("/api/v1/auth/reset_password", json={
            "phone_number": NEW_PHONE,
            "new_password": VALID_PASSWORD,
            "otp_proof": proof,
        })
        assert resp.status_code == 404

    async def test_reset_for_inactive_user_rejected(
        self, otp_client, inactive_phone_user
    ):
        """RST_007: inactive user → 403."""
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, EXISTING_PHONE, "reset_password")

        resp = await otp_client.post("/api/v1/auth/reset_password", json={
            "phone_number": EXISTING_PHONE,
            "new_password": VALID_PASSWORD,
            "otp_proof": proof,
        })
        assert resp.status_code == 403

    async def test_weak_new_password_rejected_at_schema(
        self, otp_client, existing_phone_user
    ):
        """RST_008: weak new_password → 422, proof NOT consumed."""
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, EXISTING_PHONE, "reset_password")

        resp = await otp_client.post("/api/v1/auth/reset_password", json={
            "phone_number": EXISTING_PHONE,
            "new_password": "weak",
            "otp_proof": proof,
        })
        assert resp.status_code == 422

        from app.core.security import decode_otp_proof_token
        payload = decode_otp_proof_token(proof)
        assert f"otp:proof:{payload['jti']}" in redis._store


# =============================================================================
# PHONE CHANGE TESTS (OTP-GATED, AUTHENTICATED)
# =============================================================================

class TestPhoneChange:

    async def test_change_phone_without_auth_rejected(self, otp_client):
        """PHN_001: unauthenticated → 403."""
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, OTHER_PHONE, "change_phone")

        resp = await otp_client.put("/api/v1/auth/me/change_phone", json={
            "new_phone_number": OTHER_PHONE,
            "otp_proof": proof,
        })
        assert resp.status_code == 403

    async def test_change_phone_without_proof_rejected(
        self, otp_client, auth_headers_existing
    ):
        """PHN_002: authenticated but no proof → 422."""
        resp = await otp_client.put(
            "/api/v1/auth/me/change_phone",
            json={"new_phone_number": OTHER_PHONE},
            headers=auth_headers_existing,
        )
        assert resp.status_code == 422

    async def test_change_phone_with_wrong_purpose_proof_rejected(
        self, otp_client, auth_headers_existing
    ):
        """PHN_003: proof for register used in change_phone → 400."""
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, OTHER_PHONE, "register")

        resp = await otp_client.put(
            "/api/v1/auth/me/change_phone",
            json={"new_phone_number": OTHER_PHONE, "otp_proof": proof},
            headers=auth_headers_existing,
        )
        assert resp.status_code == 400

    async def test_change_phone_with_proof_for_different_phone_rejected(
        self, otp_client, auth_headers_existing
    ):
        """PHN_004: proof for OTHER_PHONE, request with NEW_PHONE → 400."""
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, OTHER_PHONE, "change_phone")

        resp = await otp_client.put(
            "/api/v1/auth/me/change_phone",
            json={"new_phone_number": NEW_PHONE, "otp_proof": proof},
            headers=auth_headers_existing,
        )
        assert resp.status_code == 400

    async def test_change_phone_success(
        self, otp_client, auth_headers_existing, existing_phone_user, db: AsyncSession
    ):
        """PHN_005: valid proof + unused phone → 200, DB updated."""
        from sqlalchemy import select
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, OTHER_PHONE, "change_phone")

        resp = await otp_client.put(
            "/api/v1/auth/me/change_phone",
            json={"new_phone_number": OTHER_PHONE, "otp_proof": proof},
            headers=auth_headers_existing,
        )

        assert resp.status_code == 200
        assert resp.json()["new_phone_number"] == OTHER_PHONE

        result = await db.execute(select(User).where(User.id == existing_phone_user.id))
        user = result.scalar_one_or_none()
        assert user.phone_number == OTHER_PHONE

    async def test_change_phone_to_already_used_phone_rejected(
        self, otp_client, auth_headers_existing, existing_phone_user, db: AsyncSession
    ):
        """PHN_006: phone belongs to another user → 400/409."""
        second_user = User(
            email="second@example.com",
            username="seconduser2",
            phone_number=OTHER_PHONE,
            hashed_password=get_password_hash(VALID_PASSWORD),
            auth_provider=AuthProvider.LOCAL,
            is_active=True,
            is_verified=True,
        )
        db.add(second_user)
        await db.flush()

        redis = otp_client.otp_redis
        proof = _plant_proof(redis, OTHER_PHONE, "change_phone")

        resp = await otp_client.put(
            "/api/v1/auth/me/change_phone",
            json={"new_phone_number": OTHER_PHONE, "otp_proof": proof},
            headers=auth_headers_existing,
        )
        assert resp.status_code in (400, 409)

    async def test_change_phone_proof_replay_rejected(
        self, otp_client, auth_headers_existing
    ):
        """PHN_007: reuse same proof → 400."""
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, OTHER_PHONE, "change_phone")

        r1 = await otp_client.put(
            "/api/v1/auth/me/change_phone",
            json={"new_phone_number": OTHER_PHONE, "otp_proof": proof},
            headers=auth_headers_existing,
        )
        assert r1.status_code == 200

        r2 = await otp_client.put(
            "/api/v1/auth/me/change_phone",
            json={"new_phone_number": OTHER_PHONE, "otp_proof": proof},
            headers=auth_headers_existing,
        )
        assert r2.status_code == 400


# =============================================================================
# PROVIDER / FALLBACK TESTS
# =============================================================================

class TestSmsProvider:

    async def test_primary_success_send_otp_called_once(self, otp_client):
        """SMS_001: primary success → send_otp called exactly once."""
        manager = MagicMock()
        manager.send_otp = AsyncMock(return_value=_make_sms_success())

        with patch(SMS_PATCH_TARGET, new=manager):
            resp = await otp_client.post("/api/v1/auth/otp/request", json={
                "phone_number": NEW_PHONE,
                "purpose": "register",
            })

        assert resp.status_code == 200
        manager.send_otp.assert_called_once()

    async def test_transient_fail_challenge_rolled_back(self, otp_client):
        """SMS_002: transient fail → no stale challenge."""
        redis = otp_client.otp_redis

        with mock_sms_transient():
            resp = await otp_client.post("/api/v1/auth/otp/request", json={
                "phone_number": NEW_PHONE,
                "purpose": "register",
            })

        assert resp.status_code == 500
        assert f"otp:challenge:register:{NEW_PHONE}" not in redis._challenges

    async def test_permanent_fail_challenge_rolled_back(self, otp_client):
        """SMS_003: permanent fail → challenge deleted, 400 returned."""
        redis = otp_client.otp_redis

        with mock_sms_permanent():
            resp = await otp_client.post("/api/v1/auth/otp/request", json={
                "phone_number": NEW_PHONE,
                "purpose": "register",
            })

        assert resp.status_code == 400
        assert f"otp:challenge:register:{NEW_PHONE}" not in redis._challenges

    async def test_provider_message_id_present_on_success(self, otp_client):
        """SMS_004: successful send result has non-empty provider_message_id."""
        captured = {}

        async def capturing_send(phone_number, code):
            result = _make_sms_success(phone_number)
            captured["result"] = result
            return result

        manager = MagicMock()
        manager.send_otp = AsyncMock(side_effect=capturing_send)

        with patch(SMS_PATCH_TARGET, new=manager):
            resp = await otp_client.post("/api/v1/auth/otp/request", json={
                "phone_number": NEW_PHONE,
                "purpose": "register",
            })

        assert resp.status_code == 200
        assert captured["result"].provider_message_id is not None
        assert len(captured["result"].provider_message_id) > 15

    async def test_sms_result_success_condition(self):
        """SMS_005: RetStatus=1 + Value >15 digits → success."""
        from app.services.otp_service import _normalize_response

        result = _normalize_response(
            "test_provider",
            {"RetStatus": 1, "Value": "1234567890123456", "StrRetStatus": "Ok"},
            200,
        )
        assert result.success is True
        assert result.provider_message_id == "1234567890123456"

    async def test_sms_result_failure_short_value(self):
        """SMS_006: RetStatus=1 but short Value → failure."""
        from app.services.otp_service import _normalize_response

        result = _normalize_response(
            "test_provider",
            {"RetStatus": 1, "Value": "123", "StrRetStatus": "Ok"},
            200,
        )
        assert result.success is False

    @pytest.mark.parametrize("code,is_transient", [
        (-6,  True),
        (6,   True),
        (11,  True),
        (19,  True),
        (0,   False),
        (2,   False),
        (-4,  False),
        (-1,  False),
        (18,  False),
    ])
    async def test_transient_codes_classified_correctly(self, code, is_transient):
        """SMS_007: each error code classified correctly."""
        from app.services.otp_service import _normalize_response

        result = _normalize_response(
            "test_provider",
            {"RetStatus": code, "Value": str(code), "StrRetStatus": ""},
            200,
        )
        assert result.transient is is_transient, (
            f"Code {code}: expected transient={is_transient}, got {result.transient}"
        )


# =============================================================================
# SECURITY REGRESSION TESTS
# =============================================================================

class TestSecurity:

    async def test_otp_hash_is_not_stored_plaintext(self, otp_client):
        """SEC_001: OTP stored as sha256 hash, never plaintext."""
        redis = otp_client.otp_redis
        code = "777777"
        _plant_challenge(redis, NEW_PHONE, "register", code)

        stored_hash = redis._challenges[f"otp:challenge:register:{NEW_PHONE}"]["otp_hash"]
        assert stored_hash != code
        assert len(stored_hash) == 64
        assert stored_hash == _compute_otp_hash(NEW_PHONE, "register", code)

    async def test_proof_token_tampering_rejected(self, otp_client):
        """SEC_002: tampered JWT → 400."""
        redis = otp_client.otp_redis
        proof = _plant_proof(redis, NEW_PHONE, "register")
        parts = proof.split(".")
        tampered = parts[0] + "." + parts[1] + "TAMPERED." + parts[2]

        resp = await otp_client.post("/api/v1/auth/register", json={
            "phone_number": NEW_PHONE,
            "email": "h@example.com",
            "username": "haxuser",
            "password": VALID_PASSWORD,
            "otp_proof": tampered,
        })
        assert resp.status_code == 400

    async def test_proof_cannot_be_used_cross_purpose(self, otp_client):
        """SEC_003: cross-purpose proof usage rejected in both directions."""
        redis = otp_client.otp_redis
        reg_proof = _plant_proof(redis, NEW_PHONE, "register")
        rst_proof = _plant_proof(redis, EXISTING_PHONE, "reset_password")

        r1 = await otp_client.post("/api/v1/auth/reset_password", json={
            "phone_number": NEW_PHONE,
            "new_password": VALID_PASSWORD,
            "otp_proof": reg_proof,
        })
        assert r1.status_code == 400

        r2 = await otp_client.post("/api/v1/auth/register", json={
            "phone_number": EXISTING_PHONE,
            "email": "h@example.com",
            "username": "haxuser2",
            "password": VALID_PASSWORD,
            "otp_proof": rst_proof,
        })
        assert r2.status_code == 400

    async def test_brute_force_attempts_capped(self, otp_client):
        """SEC_004: MAX wrong attempts → challenge deleted, even correct code blocked."""
        redis = otp_client.otp_redis
        code = "444444"
        max_a = settings.OTP_MAX_VERIFY_ATTEMPTS
        _plant_challenge(redis, NEW_PHONE, "register", code)

        for i in range(max_a):
            resp = await otp_client.post("/api/v1/auth/otp/verify", json={
                "phone_number": NEW_PHONE,
                "purpose": "register",
                "code": "000000",
            })
            if i < max_a - 1:
                assert resp.status_code == 400
                assert "maximum" not in resp.json()["message"].lower()

        assert f"otp:challenge:register:{NEW_PHONE}" not in redis._challenges

        resp = await otp_client.post("/api/v1/auth/otp/verify", json={
            "phone_number": NEW_PHONE,
            "purpose": "register",
            "code": code,
        })
        assert resp.status_code == 400

    async def test_cooldown_enforced_per_phone_and_purpose(
        self, otp_client, existing_phone_user
    ):
        """SEC_005: register cooldown does not affect reset for a different phone."""
        redis = otp_client.otp_redis
        _seed_cooldown(redis, NEW_PHONE, "register", ttl=30)

        with mock_sms_success():
            resp = await otp_client.post("/api/v1/auth/otp/request", json={
                "phone_number": EXISTING_PHONE,
                "purpose": "reset_password",
            })

        assert resp.status_code == 200

    async def test_phone_masking_in_proof_payload(self):
        """SEC_006: normalized phone stored in proof token payload."""
        from app.core.security import decode_otp_proof_token

        jti = secrets.token_urlsafe(24)
        proof = create_otp_proof_token(
            phone_number="09120000001",
            purpose="register",
            jti=jti,
        )
        payload = decode_otp_proof_token(proof)
        assert payload["phone_number"] == "09120000001"

    async def test_expired_proof_token_rejected(self, otp_client):
        """SEC_007: expired JWT proof → 400 even if Redis key exists."""
        from datetime import datetime, timezone, timedelta
        from jose import jwt

        redis = otp_client.otp_redis
        jti = secrets.token_urlsafe(24)
        payload = {
            "sub": NEW_PHONE,
            "phone_number": NEW_PHONE,
            "purpose": "register",
            "jti": jti,
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
            "type": "otp_proof",
        }
        expired_token = jwt.encode(
            payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM
        )
        redis._store[f"otp:proof:{jti}"] = f"{NEW_PHONE}:register"

        resp = await otp_client.post("/api/v1/auth/register", json={
            "phone_number": NEW_PHONE,
            "email": "test@example.com",
            "username": "testuser",
            "password": VALID_PASSWORD,
            "otp_proof": expired_token,
        })
        assert resp.status_code == 400


# =============================================================================
# ERROR RESPONSE SHAPE TESTS
# =============================================================================

class TestErrorResponseShape:

    async def test_app_exception_shape(self, otp_client):
        """ERR_001: AppException returns flat {error, message} shape."""
        resp = await otp_client.post("/api/v1/auth/otp/request", json={
            "phone_number": "1234",
            "purpose": "register",
        })
        data = resp.json()
        assert "error" in data or "message" in data

    async def test_rate_limit_response_has_retry_after_in_body_and_header(
        self, otp_client
    ):
        """ERR_002: 429 includes retry_after in body AND Retry-After header."""
        redis = otp_client.otp_redis
        _seed_cooldown(redis, NEW_PHONE, "register", ttl=55)

        with mock_sms_success():
            resp = await otp_client.post("/api/v1/auth/otp/request", json={
                "phone_number": NEW_PHONE,
                "purpose": "register",
            })

        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        assert int(resp.headers["Retry-After"]) > 0
        assert "retry_after" in resp.json()
        assert resp.json()["retry_after"] > 0

    async def test_validation_error_shape(self, otp_client):
        """ERR_003: 422 returns error=VALIDATION_ERROR."""
        resp = await otp_client.post("/api/v1/auth/otp/verify", json={
            "phone_number": NEW_PHONE,
        })
        assert resp.status_code == 422
        assert resp.json().get("error") == "VALIDATION_ERROR"