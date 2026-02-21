# app/core/llm_client.py
import time
import traceback
import asyncio
from concurrent.futures import ThreadPoolExecutor
import structlog
import json
from google import genai
from google.genai import types
from app.config import settings

logger = structlog.get_logger()

class LLMClient:
    """
    Two sync clients + thread pool + automatic retry.
    """
    
    def __init__(self, api_key: str, base_url: str):
        opts = {"base_url": base_url}
        
        self.clients = {
            "enhancer": genai.Client(api_key=api_key, http_options=opts),
            "generator": genai.Client(api_key=api_key, http_options=opts),
        }
        
        self.timeouts = {
            "enhancer": settings.LLM_TIMEOUT_SECONDS,
            "generator": settings.LLM_TIMEOUT_SECONDS,
        }
        
        self.max_retries = settings.LLM_MAX_RETRY
        self._pool = ThreadPoolExecutor(max_workers=250, thread_name_prefix="llm")
        logger.info("✓ LLMClient ready (with retry)")
    
    async def generate(
        self,
        model: str,
        system_instruction: str,
        content: str,
        temperature: float,
        top_p: float,
        role: str = "generator",
        thinking_budget: int = 0,
    ) -> str:
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
                timeout=timeout,
                content_length=len(content)//4 if content else 0,
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
                result = response.text.strip()
                
                logger.info(
                    "LLM API success",
                    request_id=request_id,
                    role=role,
                    api_time_ms=round((time.perf_counter() - t_api) * 1000, 2),
                    result_length=len(result)//4,
                )
                return result
            
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
                        next_attempt=attempt + 2,
                        time_elapsed_ms=elapsed,
                    )
                    await asyncio.sleep(0.5)
                else:
                    logger.error(
                        "LLM all retries failed",
                        request_id=base_request_id,
                        role=role,
                        attempts=self.max_retries,
                    )
            
            except Exception as e:
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
    
    def shutdown(self):
        self._pool.shutdown(wait=False)
        