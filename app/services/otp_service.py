# app/services/otp_service.py
"""
OTP service + Melipayamak REST SMS provider — no SDK required.
Uses httpx.AsyncClient for truly non-blocking HTTP under high concurrency.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
import redis.asyncio as aioredis
import structlog

from app.config import settings
from app.core.security import create_otp_proof_token, decode_otp_proof_token
from app.middleware.exceptions import BadRequestException, InternalException, RateLimitException

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# OTP Purpose
# ---------------------------------------------------------------------------

class OTPPurpose(str, Enum):
    register = "register"
    reset_password = "reset_password"


# ---------------------------------------------------------------------------
# SMS provider — result type
# ---------------------------------------------------------------------------

@dataclass
class SmsSendResult:
    success: bool
    provider: str
    provider_message_id: Optional[str] = None
    error_code: Optional[int] = None
    error_message: Optional[str] = None
    transient: bool = False
    raw: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Melipayamak status code catalogue
# ---------------------------------------------------------------------------

_STATUS_MESSAGES: Dict[int, str] = {
    -110: "Use API key instead of password",
    -109: "Allowed IP is required for API usage",
    -108: "IP blocked due to failed API attempts",
    -10: "Payload contains link in variables",
    -7: "Sender number error",
    -6: "Internal provider error",
    -5: "Message variables mismatch with approved template",
    -4: "Template body ID invalid or not approved",
    -3: "Sender line is not defined",
    -2: "Number count limit exceeded",
    -1: "Access to this API is disabled",
    0: "Username or password is invalid",
    2: "Insufficient credit",
    6: "Provider is updating",
    7: "Message contains filtered words",
    10: "Target user is not active",
    11: "Not sent",
    12: "User documents are incomplete",
    16: "No receiver found",
    17: "Message text is empty",
    18: "Receiver number is invalid",
    19: "Hourly sending limit exceeded",
}

_TRANSIENT_CODES: frozenset[int] = frozenset({-6, 6, 11, 19})


# ---------------------------------------------------------------------------
# Shared async HTTP client (module-level singleton)
# One client = one connection pool shared across ALL requests
# ---------------------------------------------------------------------------

# limits: how many concurrent connections httpx keeps alive
_http_client: Optional[httpx.AsyncClient] = None


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=5.0,          # time to establish connection
                read=settings.SMS_PROVIDER_TIMEOUT_SECONDS,
                write=5.0,
                pool=3.0,             # time to wait for a free connection from pool
            ),
            limits=httpx.Limits(
                max_connections=100,       # total open connections
                max_keepalive_connections=20,  # kept alive for reuse
                keepalive_expiry=30.0,
            ),
        )
    return _http_client


@asynccontextmanager
async def lifespan_http_client() -> AsyncGenerator[None, None]:
    """
    Use this in your FastAPI lifespan to properly open/close the client.

    Example in main.py:
        from contextlib import asynccontextmanager
        from app.services.otp_service import lifespan_http_client

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            async with lifespan_http_client():
                yield

        app = FastAPI(lifespan=lifespan)
    """
    get_http_client()  # initialize on startup
    try:
        yield
    finally:
        if _http_client and not _http_client.is_closed:
            await _http_client.aclose()


# ---------------------------------------------------------------------------
# Internal HTTP helper — fully async, no threads
# ---------------------------------------------------------------------------

async def _post_with_retries(
    provider_name: str,
    url: str,
    form_payload: Dict[str, Any],
) -> SmsSendResult:
    max_retries = max(settings.SMS_PROVIDER_MAX_RETRIES, 0)
    backoff = max(settings.SMS_PROVIDER_RETRY_BACKOFF_SECONDS, 0.1)
    client = get_http_client()

    for attempt in range(max_retries + 1):
        try:
            response = await client.post(url, data=form_payload)
            payload = _safe_parse_response(response)
            result = _normalize_response(provider_name, payload, response.status_code)

            if result.success or not result.transient or attempt == max_retries:
                return result

            await asyncio.sleep(backoff * (2 ** attempt))  # type: ignore[name-defined]

        except httpx.TimeoutException:
            if attempt == max_retries:
                return SmsSendResult(
                    success=False,
                    provider=provider_name,
                    error_message="SMS provider request timed out",
                    transient=True,
                )
            await asyncio.sleep(backoff * (2 ** attempt))  # type: ignore[name-defined]

        except httpx.RequestError as exc:
            if attempt == max_retries:
                return SmsSendResult(
                    success=False,
                    provider=provider_name,
                    error_message=f"Network error: {exc}",
                    transient=True,
                )
            await asyncio.sleep(backoff * (2 ** attempt))  # type: ignore[name-defined]

    return SmsSendResult(success=False, provider=provider_name, transient=True)


def _safe_parse_response(response: httpx.Response) -> Dict[str, Any]:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {"raw": data}
    except Exception:
        return {"raw_text": (response.text or "").strip()}


def _normalize_response(
    provider: str, payload: Dict[str, Any], http_status: int
) -> SmsSendResult:
    if "RetStatus" in payload:
        code = int(payload["RetStatus"])
        value = str(payload.get("Value", ""))
        status_text = str(payload.get("StrRetStatus", ""))

        if code == 1 and value.isdigit() and len(value) > 15:
            return SmsSendResult(
                success=True, provider=provider, provider_message_id=value, raw=payload
            )
        return SmsSendResult(
            success=False,
            provider=provider,
            error_code=code,
            error_message=_STATUS_MESSAGES.get(code, status_text or "SMS send failed"),
            transient=code in _TRANSIENT_CODES or http_status >= 500,
            raw=payload,
        )

    raw_text = str(payload.get("raw_text", "")).strip()
    if raw_text.lstrip("-").isdigit():
        code = int(raw_text)
        if code > 10 ** 15:
            return SmsSendResult(
                success=True, provider=provider, provider_message_id=str(code), raw=payload
            )
        return SmsSendResult(
            success=False,
            provider=provider,
            error_code=code,
            error_message=_STATUS_MESSAGES.get(code, "SMS send failed"),
            transient=code in _TRANSIENT_CODES or http_status >= 500,
            raw=payload,
        )

    return SmsSendResult(
        success=False,
        provider=provider,
        error_message=f"Unexpected provider response: {payload}",
        transient=http_status >= 500,
        raw=payload,
    )


# ---------------------------------------------------------------------------
# Melipayamak REST provider
# ---------------------------------------------------------------------------

class _MelipayamakProvider:
    name = "melipayamak_rest"

    async def send_otp(self, phone_number: str, code: str) -> SmsSendResult:
        u = settings.MELIPAYAMAK_USERNAME
        p = settings.MELIPAYAMAK_PASSWORD
        b = settings.MELIPAYAMAK_BODY_ID

        if not (u and p and b):
            return SmsSendResult(
                success=False,
                provider=self.name,
                error_message="SMS provider is not configured",
            )

        return await _post_with_retries(
            provider_name=self.name,
            url=settings.MELIPAYAMAK_REST_URL,
            form_payload={
                "username": u,
                "password": p,
                "text": code,
                "to": phone_number,
                "bodyId": b,
            },
        )


# ---------------------------------------------------------------------------
# Provider manager
# ---------------------------------------------------------------------------

@dataclass
class _SmsManager:
    _providers: List[_MelipayamakProvider] = field(
        default_factory=lambda: [_MelipayamakProvider()]
    )

    async def send_otp(self, phone_number: str, code: str) -> SmsSendResult:
        last: Optional[SmsSendResult] = None

        for idx, provider in enumerate(self._providers):
            result = await provider.send_otp(phone_number, code)
            if result.success:
                return result

            last = result
            logger.warning(
                "sms_provider_failed",
                provider=result.provider,
                error_code=result.error_code,
                error_message=result.error_message,
                transient=result.transient,
            )

            if idx == len(self._providers) - 1 or not result.transient:
                break

        return last or SmsSendResult(
            success=False,
            provider="unknown",
            error_message="SMS provider unavailable",
            transient=True,
        )


# ---------------------------------------------------------------------------
# OTP Service
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402 — placed here to keep httpx section clean above


class OtpService:
    OTP_KEY_PREFIX = "otp:challenge:"
    OTP_COOLDOWN_PREFIX = "otp:cooldown:"
    OTP_PROOF_PREFIX = "otp:proof:"

    _sms = _SmsManager()

    @classmethod
    def normalize_phone(cls, phone_number: str) -> str:
        phone = str(phone_number).strip()
        if phone.startswith("+98"):
            return "0" + phone[3:]
        if phone.startswith("98"):
            return "0" + phone[2:]
        return phone

    @classmethod
    async def request_otp(
        cls, redis: aioredis.Redis, phone_number: str, purpose: OTPPurpose
    ) -> None:
        phone = cls.normalize_phone(phone_number)
        cooldown_key = cls._cooldown_key(phone, purpose)
        challenge_key = cls._challenge_key(phone, purpose)

        ttl = await redis.ttl(cooldown_key)
        if ttl and ttl > 0:
            raise RateLimitException(
                message=f"Please wait {ttl} seconds before requesting another OTP",
                retry_after=ttl,
            )

        code = cls._generate_code(settings.OTP_CODE_LENGTH)
        otp_hash = cls._hash_otp(phone, purpose, code)

        await redis.hset(
            challenge_key,
            mapping={
                "otp_hash": otp_hash,
                "attempts": "0",
                "created_at": str(int(time.time())),
            },
        )
        await redis.expire(challenge_key, settings.OTP_EXPIRE_SECONDS)

        result = await cls._sms.send_otp(phone_number=phone, code=code)
        if not result.success:
            await redis.delete(challenge_key)
            if result.transient:
                raise InternalException("SMS provider temporary error. Please try again shortly.")
            raise BadRequestException(result.error_message or "Failed to send OTP")

        await redis.set(cooldown_key, "1", ex=settings.OTP_RESEND_COOLDOWN_SECONDS)

        logger.info(
            "otp_sent",
            purpose=purpose,
            phone_masked=cls._mask_phone(phone),
            provider=result.provider,
            provider_message_id=result.provider_message_id,
        )

    @classmethod
    async def verify_otp_and_issue_proof(
        cls,
        redis: aioredis.Redis,
        phone_number: str,
        purpose: OTPPurpose,
        code: str,
    ) -> str:
        phone = cls.normalize_phone(phone_number)
        challenge_key = cls._challenge_key(phone, purpose)
        challenge = await redis.hgetall(challenge_key)

        if not challenge:
            raise BadRequestException("OTP is expired or not requested")

        attempts = int(challenge.get("attempts", "0"))
        if attempts >= settings.OTP_MAX_VERIFY_ATTEMPTS:
            await redis.delete(challenge_key)
            raise BadRequestException("Maximum OTP verification attempts exceeded")

        submitted_hash = cls._hash_otp(phone, purpose, code)
        if submitted_hash != challenge.get("otp_hash"):
            new_attempts = await redis.hincrby(challenge_key, "attempts", 1)
            remaining = settings.OTP_MAX_VERIFY_ATTEMPTS - int(new_attempts)
            if remaining <= 0:
                await redis.delete(challenge_key)
                raise BadRequestException("Maximum OTP verification attempts exceeded")
            raise BadRequestException(f"Invalid OTP code. {remaining} attempts remaining.")

        await redis.delete(challenge_key)

        jti = secrets.token_urlsafe(24)
        proof_token = create_otp_proof_token(phone_number=phone, purpose=purpose, jti=jti)
        await redis.set(
            cls._proof_key(jti),
            f"{phone}:{purpose}",
            ex=settings.OTP_VERIFY_TOKEN_EXPIRE_MINUTES * 60,
        )

        logger.info("otp_verified", purpose=purpose, phone_masked=cls._mask_phone(phone))
        return proof_token

    @classmethod
    async def consume_verification_proof(
        cls,
        redis: aioredis.Redis,
        proof_token: str,
        expected_phone: str,
        expected_purpose: OTPPurpose,
    ) -> None:
        jti = cls._validate_proof_payload(proof_token, expected_phone, expected_purpose)
        if not await redis.exists(cls._proof_key(jti)):
            raise BadRequestException("OTP proof expired or already used")
        await redis.delete(cls._proof_key(jti))

    @classmethod
    async def validate_proof_without_consuming(
        cls,
        redis: aioredis.Redis,
        proof_token: str,
        expected_phone: str,
        expected_purpose: OTPPurpose,
    ) -> None:
        """Validates proof is real, unexpired, and bound — does NOT consume it."""
        jti = cls._validate_proof_payload(proof_token, expected_phone, expected_purpose)
        if not await redis.exists(cls._proof_key(jti)):
            raise BadRequestException("OTP proof expired or already used")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @classmethod
    def _validate_proof_payload(
        cls, proof_token: str, expected_phone: str, expected_purpose: OTPPurpose
    ) -> str:
        payload = decode_otp_proof_token(proof_token)
        if not payload:
            raise BadRequestException("Invalid OTP proof")

        phone = cls.normalize_phone(expected_phone)
        token_phone = cls.normalize_phone(payload.get("phone_number", ""))
        jti: str = payload.get("jti", "")

        if not jti or token_phone != phone or payload.get("purpose") != expected_purpose:
            raise BadRequestException("OTP proof does not match this request")

        return jti

    @classmethod
    def _challenge_key(cls, phone: str, purpose: str) -> str:
        return f"{cls.OTP_KEY_PREFIX}{purpose}:{phone}"

    @classmethod
    def _cooldown_key(cls, phone: str, purpose: str) -> str:
        return f"{cls.OTP_COOLDOWN_PREFIX}{purpose}:{phone}"

    @classmethod
    def _proof_key(cls, jti: str) -> str:
        return f"{cls.OTP_PROOF_PREFIX}{jti}"

    @classmethod
    def _generate_code(cls, length: int) -> str:
        return f"{secrets.randbelow(10 ** length):0{length}d}"

    @classmethod
    def _hash_otp(cls, phone: str, purpose: str, code: str) -> str:
        raw = f"{phone}:{purpose}:{code}:{settings.SECRET_KEY}"
        return hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def _mask_phone(cls, phone: str) -> str:
        return phone[:4] + "****" + phone[-2:] if len(phone) >= 4 else "***"