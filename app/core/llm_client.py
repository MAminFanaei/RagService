# app/core/llm_client.py
import time
import traceback
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any
import structlog
from google import genai
from google.genai import types
from app.config import settings

logger = structlog.get_logger()

# ── Transient errors worth retrying ──
RETRYABLE_ERROR_SUBSTRINGS = (
    "RemoteProtocolError",
    "Server disconnected",
    "ConnectionReset",
    "Connection reset",
    "ServiceUnavailable",
    "503",
    "429",
    "overloaded",
)

def _is_retryable(exc: Exception) -> bool:
    exc_str = f"{type(exc).__name__}: {str(exc)}"
    return (
        isinstance(exc, (ConnectionError, ConnectionResetError, TimeoutError))
        or any(sub in exc_str for sub in RETRYABLE_ERROR_SUBSTRINGS)
    )


class LLMClient:
    """
    Two sync clients + thread pool + automatic retry + usage tracking.
    """

    def __init__(self, api_key: str, base_url: str):
        self.max_retries = settings.LLM_MAX_RETRY
        self._pool = ThreadPoolExecutor(max_workers=250, thread_name_prefix="llm")
        self.timeouts = {
            "enhancer": settings.LLM_TIMEOUT_SECONDS,
            "generator": settings.LLM_TIMEOUT_SECONDS,
        }

        opts = {"base_url": base_url}
        self.clients = {
            "enhancer": genai.Client(api_key=api_key, http_options=opts),
            "generator": genai.Client(api_key=api_key, http_options=opts),
        }
        logger.info("✓ LLMClient ready (with retry)")

    @staticmethod
    def extract_usage(response) -> Dict[str, Any]:
        usage: Dict[str, Any] = {
            "input_prompt_token": 0,
            "pure_output_token": 0,
            "thoughts_token": 0,
            "total_token": 0,
            "cached_token": 0,
        }
        meta = getattr(response, "usage_metadata", None)
        if meta is None:
            return usage
        usage["input_prompt_token"] = getattr(meta, "prompt_token_count", 0) or 0
        usage["pure_output_token"] = getattr(meta, "candidates_token_count", 0) or 0
        usage["thoughts_token"] = getattr(meta, "thoughts_token_count", 0) or 0
        usage["total_token"] = getattr(meta, "total_token_count", 0) or 0
        usage["cached_token"] = getattr(meta, "cached_content_token_count", 0) or 0
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
        client = self.clients[role]
        timeout = self.timeouts[role]
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

            def _call():
                t_call = time.perf_counter()
                logger.info(
                    "LLM thread started",
                    request_id=request_id,
                    role=role,
                    thread_delay_ms=round((t_call - t_start) * 1000, 2),
                )

                config = types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=temperature,
                    top_p=top_p,
                )
                if thinking_budget > 0:
                    config.thinking_config = types.ThinkingConfig(
                        thinking_budget=thinking_budget
                    )

                t_api = time.perf_counter()
                response = client.models.generate_content(
                    model=model, config=config, contents=content
                )

                text = response.text.strip()
                usage = LLMClient.extract_usage(response)

                logger.info(
                    "LLM API success",
                    request_id=request_id,
                    role=role,
                    api_time_ms=round((time.perf_counter() - t_api) * 1000, 2),
                    usage=usage,
                )
                return {"text": text, "usage": usage}

            try:
                loop = asyncio.get_running_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(self._pool, _call),
                    timeout=timeout,
                )

                logger.info(
                    "LLM request complete",
                    request_id=request_id,
                    role=role,
                    attempt=attempt + 1,
                    total_time_ms=round((time.perf_counter() - t_start) * 1000, 2),
                )
                return result

            except asyncio.TimeoutError as e:
                last_error = e
                elapsed = round((time.perf_counter() - t_start) * 1000, 2)
                if attempt < self.max_retries - 1:
                    logger.warning(
                        "LLM timeout, retrying",
                        request_id=request_id,
                        role=role,
                        attempt=attempt + 1,
                        time_elapsed_ms=elapsed,
                    )
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    logger.error("LLM's retries failed ", role=role)

            except Exception as e:
                last_error = e
                elapsed = round((time.perf_counter() - t_start) * 1000, 2)
                if _is_retryable(e) and attempt < self.max_retries - 1:
                    wait_time = min(1.0 * (attempt + 1), 5.0)
                    logger.warning(
                        "LLM transient error, retrying",
                        request_id=request_id,
                        role=role,
                        error_type=type(e).__name__,
                        error=str(e)[:200],
                        attempt=attempt + 1,
                        time_elapsed_ms=elapsed,
                    )
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(
                        "LLM exception (non-retryable or final attempt)",
                        request_id=request_id,
                        role=role,
                        error=str(e),
                        error_type=type(e).__name__,
                        traceback=traceback.format_exc(),
                    )
                    raise

        raise last_error

    def shutdown(self):
        self._pool.shutdown(wait=False)