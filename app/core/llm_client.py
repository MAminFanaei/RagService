# app/core/llm_client.py
import time
import traceback
import asyncio
from typing import Dict, Any
import structlog
from openai import AsyncOpenAI, APIConnectionError, APITimeoutError, RateLimitError, APIStatusError
from app.config import settings

logger = structlog.get_logger()

# ── Retryable exception types (OpenAI SDK has proper exception classes) ──
RETRYABLE_EXCEPTIONS = (
    APIConnectionError,    # network issues, proxy drops
    APITimeoutError,       # request timeout
    RateLimitError,        # 429
)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _is_retryable(exc: Exception) -> bool:
    """Check if an exception is transient and worth retrying."""
    if isinstance(exc, RETRYABLE_EXCEPTIONS):
        return True
    if isinstance(exc, APIStatusError) and exc.status_code in RETRYABLE_STATUS_CODES:
        return True
    # Fallback string match for edge cases
    exc_str = f"{type(exc).__name__}: {str(exc)}"
    return any(sub in exc_str for sub in (
        "RemoteProtocolError",
        "Server disconnected",
        "Connection reset",
        "overloaded",
    ))


class LLMClient:
    """
    Async OpenAI-compatible client with retry + usage tracking.
    Native async — no thread pool needed.
    """

    def __init__(self, api_key: str, base_url: str):
        self.max_retries = settings.LLM_MAX_RETRY
        self.timeouts = {
            "enhancer": settings.LLM_TIMEOUT_SECONDS,
            "generator": settings.LLM_TIMEOUT_SECONDS,
        }

        # One client per role (separate connection pools)
        self.clients = {
            "enhancer": AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=self.timeouts["enhancer"],
                max_retries=0,  # We handle retries ourselves
            ),
            "generator": AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=self.timeouts["generator"],
                max_retries=0,
            ),
        }

    @staticmethod
    def extract_usage(response) -> Dict[str, Any]:
        """
        Extract token usage from OpenAI ChatCompletion response.
        
        OpenAI usage fields:
          - prompt_tokens           → input tokens
          - completion_tokens       → output tokens (includes reasoning)
          - total_tokens            → grand total
          - prompt_tokens_details.cached_tokens        → cached input tokens
          - completion_tokens_details.reasoning_tokens  → thinking/reasoning tokens
        
        We normalize to a consistent schema matching what we had before.
        """
        usage: Dict[str, Any] = {
            "input_prompt_token": 0,
            "pure_output_token": 0,
            "thoughts_token": 0,
            "total_token": 0,
            "cached_token": 0,
        }

        raw = getattr(response, "usage", None)
        if raw is None:
            return usage

        prompt_tokens = getattr(raw, "prompt_tokens", 0) or 0
        completion_tokens = getattr(raw, "completion_tokens", 0) or 0
        total_tokens = getattr(raw, "total_tokens", 0) or 0

        # Extract detailed breakdowns (may not exist on all providers)
        reasoning_tokens = 0
        cached_tokens = 0

        completion_details = getattr(raw, "completion_tokens_details", None)
        if completion_details:
            reasoning_tokens = getattr(completion_details, "reasoning_tokens", 0) or 0

        prompt_details = getattr(raw, "prompt_tokens_details", None)
        if prompt_details:
            cached_tokens = getattr(prompt_details, "cached_tokens", 0) or 0

        # Pure output = completion - reasoning (thinking)
        pure_output = completion_tokens - reasoning_tokens

        usage["input_prompt_token"] = prompt_tokens
        usage["pure_output_token"] = pure_output
        usage["thoughts_token"] = reasoning_tokens
        usage["total_token"] = total_tokens
        usage["cached_token"] = cached_tokens
        return usage

    async def generate(
        self,
        model: str,
        system_instruction: str,
        content: str,
        temperature: float,
        top_p: float,
        role: str = "generator",
        thinking_budget: int = 0,
    ) -> Dict[str, Any]:
        """
        Returns {"text": str, "usage": {...}}.
        Native async — no thread pool needed.
        """
        client = self.clients[role]
        base_request_id = f"{role}_{int(time.time() * 1000) % 100000}"

        last_error = None

        for attempt in range(self.max_retries):
            request_id = f"{base_request_id}_attempt_{attempt + 1}"
            t_start = time.perf_counter()

            logger.info(
                "LLM request starting",
                request_id=request_id,
                role=role,
                model=model,
                attempt=attempt + 1,
                max_retries=self.max_retries,
            )

            try:
                # ── Build request kwargs ──
                messages = [
                    {"role": "system", "content": system_instruction}, #developer
                    {"role": "user", "content": content},
                ]

                kwargs: Dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "top_p": top_p,
                }

                # Thinking budget 
                if thinking_budget > 0:
                    kwargs["extra_body"] = {"thinking": {"type": "enabled", "budget_tokens": thinking_budget} }

                # extra_body={"reasoning": {"effort": "high"}},  # O1

                # ── Make the call (native async!) ──
                t_api = time.perf_counter()
                response = await asyncio.wait_for(
                    client.chat.completions.create(**kwargs),
                    timeout=self.timeouts[role],
                )

                # ── Extract text ──
                text = response.choices[0].message.content or ""
                text = text.strip()

                # ── Extract usage ──
                usage = self.extract_usage(response)

                logger.info(
                    "LLM API success",
                    request_id=request_id,
                    role=role,
                    api_time_ms=round((time.perf_counter() - t_api) * 1000, 2),
                    usage=usage,
                )

                logger.info(
                    "LLM request complete",
                    request_id=request_id,
                    role=role,
                    attempt=attempt + 1,
                    total_time_ms=round((time.perf_counter() - t_start) * 1000, 2),
                )
                return {"text": text, "usage": usage}

            except asyncio.TimeoutError as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    logger.warning(
                        "LLM timeout, retrying",
                        request_id=request_id,
                        role=role,
                        attempt=attempt + 1,
                    )
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    logger.error("LLM all retries failed (timeout)", role=role)

            except Exception as e:
                last_error = e

                if _is_retryable(e) and attempt < self.max_retries - 1:
                    wait_time = min(1.0 * (attempt + 1), 5.0)
                    logger.warning(
                        "LLM transient error, retrying",
                        request_id=request_id,
                        role=role,
                        error_type=type(e).__name__,
                        error=str(e)[:250],
                        attempt=attempt + 1,
                        retry_in_seconds=wait_time,
                    )
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(
                        "LLM exception",
                        request_id=request_id,
                        role=role,
                        error=str(e),
                        error_type=type(e).__name__,
                        traceback=traceback.format_exc(),
                    )
                    raise

        raise last_error

    async def shutdown(self):
        """Close async clients gracefully."""
        for role, client in self.clients.items():
            await client.close()