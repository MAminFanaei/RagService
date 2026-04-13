# app/services/sms_provider_service.py
import asyncio
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
import requests
import structlog

from app.config import settings

logger = structlog.get_logger()

# Provider status code map
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

_TRANSIENT_CODES = {-6, 6, 11, 19}


@dataclass
class SmsSendResult:
    success: bool
    provider: str
    provider_message_id: Optional[str] = None
    error_code: Optional[int] = None
    error_message: Optional[str] = None
    transient: bool = False
    raw: Optional[Dict[str, Any]] = None


class MelipayamakRestCredentialProvider:
    name = "melipayamak_rest"

    def __init__(self):
        self.username = settings.MELIPAYAMAK_USERNAME
        self.password = settings.MELIPAYAMAK_PASSWORD
        self.body_id = settings.MELIPAYAMAK_BODY_ID
        self.url = settings.MELIPAYAMAK_REST_URL

    async def send_otp(self, phone_number: str, code: str) -> SmsSendResult:
        if not self.username or not self.password or not self.body_id:
            return SmsSendResult(
                success=False,
                provider=self.name,
                error_message="Fallback SMS provider is not configured",
                transient=False,
            )

        form_payload = {
            "username": self.username,
            "password": self.password,
            "text": code,
            "to": phone_number,
            "bodyId": self.body_id,
        }

        return await _post_with_retries(
            provider_name=self.name,
            url=self.url,
            json_payload=None,
            form_payload=form_payload,
        )


class SmsProviderManager:
    def __init__(self):
        # initialize list first, then append
        self.providers: List = []
        self.providers.append(MelipayamakRestCredentialProvider())

    async def send_otp(self, phone_number: str, code: str) -> SmsSendResult:
        last_error: Optional[SmsSendResult] = None

        for idx, provider in enumerate(self.providers):
            result = await provider.send_otp(phone_number, code)
            if result.success:
                return result

            last_error = result
            logger.warning(
                "sms_provider_failed",
                provider=result.provider,
                error_code=result.error_code,
                error_message=result.error_message,
                transient=result.transient,
            )

            is_last = idx == len(self.providers) - 1
            if is_last:
                break
            if not result.transient:
                break

        return last_error or SmsSendResult(
            success=False,
            provider="unknown",
            error_message="SMS provider unavailable",
            transient=True,
        )


async def _post_with_retries(
    provider_name: str,
    url: str,
    json_payload: Optional[Dict[str, Any]],
    form_payload: Optional[Dict[str, Any]],
) -> SmsSendResult:
    max_retries = max(settings.SMS_PROVIDER_MAX_RETRIES, 0)
    timeout = settings.SMS_PROVIDER_TIMEOUT_SECONDS
    backoff = max(settings.SMS_PROVIDER_RETRY_BACKOFF_SECONDS, 0.1)

    attempt = 0
    while True:
        try:
            response = await asyncio.to_thread(
                requests.post,
                url,
                json=json_payload,
                data=form_payload,
                timeout=timeout,
            )

            parsed = _safe_parse_response(response)
            result = _normalize_provider_response(provider_name, parsed, response.status_code)

            if result.success:
                return result

            if attempt < max_retries and result.transient:
                await asyncio.sleep(backoff * (2 ** attempt))
                attempt += 1
                continue

            return result

        except requests.RequestException as exc:
            transient_result = SmsSendResult(
                success=False,
                provider=provider_name,
                error_message=f"Network error: {str(exc)}",
                transient=True,
            )
            if attempt < max_retries:
                await asyncio.sleep(backoff * (2 ** attempt))
                attempt += 1
                continue
            return transient_result


def _safe_parse_response(response: requests.Response) -> Dict[str, Any]:
    try:
        data = response.json()
        if isinstance(data, dict):
            return data
        return {"raw": data}
    except Exception:
        text = (response.text or "").strip()
        return {"raw_text": text}


def _normalize_provider_response(
    provider: str,
    payload: Dict[str, Any],
    http_status: int,
) -> SmsSendResult:
    if "RetStatus" in payload:
        status_code = int(payload.get("RetStatus"))
        value = str(payload.get("Value", ""))
        status_text = str(payload.get("StrRetStatus", ""))

        if status_code == 1 and value.isdigit() and len(value) > 15:
            return SmsSendResult(
                success=True,
                provider=provider,
                provider_message_id=value,
                raw=payload,
            )

        normalized_code = int(status_code)
        return SmsSendResult(
            success=False,
            provider=provider,
            error_code=normalized_code,
            error_message=_STATUS_MESSAGES.get(normalized_code, status_text or "SMS send failed"),
            transient=normalized_code in _TRANSIENT_CODES or http_status >= 500,
            raw=payload,
        )

    raw_text = str(payload.get("raw_text", "")).strip()
    if raw_text.lstrip("-").isdigit():
        code = int(raw_text)
        if code > 10 ** 15:
            return SmsSendResult(
                success=True,
                provider=provider,
                provider_message_id=str(code),
                raw=payload,
            )
        normalized_code = int(code)
        return SmsSendResult(
            success=False,
            provider=provider,
            error_code=normalized_code,
            error_message=_STATUS_MESSAGES.get(normalized_code, "SMS send failed"),
            transient=normalized_code in _TRANSIENT_CODES or http_status >= 500,
            raw=payload,
        )

    return SmsSendResult(
        success=False,
        provider=provider,
        error_message=f"Unexpected provider response: {payload}",
        transient=http_status >= 500,
        raw=payload,
    )