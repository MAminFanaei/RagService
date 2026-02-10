# profiling/middleware_timing.py
"""
Request Timing Middleware

Add this to your FastAPI app to see detailed timing for each request.
Shows exactly where time is spent.
"""

import time
from typing import Callable
from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
import structlog

logger = structlog.get_logger()


class TimingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that logs detailed timing for each request.
    
    Add to your app with:
        app.add_middleware(TimingMiddleware)
    """
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Start timing
        start_time = time.perf_counter()
        
        # Track component times via request state
        request.state.timing = {
            "start": start_time,
            "checkpoints": []
        }
        
        # Process request
        response = await call_next(request)
        
        # Calculate total time
        total_time = (time.perf_counter() - start_time) * 1000
        
        # Add timing header
        response.headers["X-Response-Time-Ms"] = f"{total_time:.2f}"
        
        # Log slow requests
        if total_time > 1000:  # Over 1 second
            logger.warning(
                "Slow request",
                path=request.url.path,
                method=request.method,
                time_ms=f"{total_time:.2f}",
                checkpoints=getattr(request.state, 'timing', {}).get('checkpoints', [])
            )
        else:
            logger.info(
                "Request completed",
                path=request.url.path,
                method=request.method,
                time_ms=f"{total_time:.2f}"
            )
        
        return response


def add_checkpoint(request: Request, name: str):
    """
    Add a timing checkpoint within a request.
    
    Usage in endpoint:
        from profiling.middleware_timing import add_checkpoint
        
        @router.post("/messages")
        async def send_message(request: Request, ...):
            add_checkpoint(request, "auth_complete")
            # do work
            add_checkpoint(request, "rag_query_start")
            result = await rag_engine.query(...)
            add_checkpoint(request, "rag_query_end")
    """
    if hasattr(request.state, 'timing'):
        elapsed = (time.perf_counter() - request.state.timing["start"]) * 1000
        request.state.timing["checkpoints"].append({
            "name": name,
            "elapsed_ms": elapsed
        })


# ============================================================================
# DETAILED ENDPOINT PROFILER (decorator version)
# ============================================================================

from functools import wraps
import asyncio


def profile_endpoint(func):
    """
    Decorator to add detailed profiling to an endpoint.
    
    Usage:
        @router.post("/messages")
        @profile_endpoint
        async def send_message(...):
            ...
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()
        timings = {}
        
        # Store in context for nested timing
        if 'request' in kwargs:
            kwargs['request'].state.profile_timings = timings
        
        try:
            result = await func(*args, **kwargs)
            return result
        finally:
            total = (time.perf_counter() - start) * 1000
            
            # Log detailed timings
            if timings:
                logger.info(
                    "Endpoint profile",
                    endpoint=func.__name__,
                    total_ms=f"{total:.2f}",
                    breakdown=timings
                )
    
    return wrapper


class ProfileBlock:
    """
    Context manager for profiling a block of code.
    
    Usage:
        async with ProfileBlock(request, "rag_query"):
            result = await rag_engine.query(...)
    """
    
    def __init__(self, request: Request, name: str):
        self.request = request
        self.name = name
        self.start = None
    
    async def __aenter__(self):
        self.start = time.perf_counter()
        return self
    
    async def __aexit__(self, *args):
        elapsed = (time.perf_counter() - self.start) * 1000
        
        if hasattr(self.request.state, 'profile_timings'):
            self.request.state.profile_timings[self.name] = elapsed
        
        add_checkpoint(self.request, f"{self.name}: {elapsed:.1f}ms")


# ============================================================================
# INTEGRATION EXAMPLE
# ============================================================================

INTEGRATION_EXAMPLE = '''
# In app/main.py, add:

from profiling.middleware_timing import TimingMiddleware

app = FastAPI(...)
app.add_middleware(TimingMiddleware)  # Add this line


# In app/api/v1/chats.py, for detailed profiling:

from profiling.middleware_timing import ProfileBlock, add_checkpoint

@router.post("/{chat_id}/messages")
async def send_message(
    chat_id: str,
    message: MessageCreate,
    request: Request,  # Add Request parameter
    ...
):
    add_checkpoint(request, "start")
    
    # Rate limit check
    async with ProfileBlock(request, "rate_limit"):
        # rate limit code...
        pass
    
    # RAG query
    async with ProfileBlock(request, "rag_query"):
        result = await RAGService.process_query(...)
    
    add_checkpoint(request, "end")
    return result
'''

if __name__ == "__main__":
    print("This module provides timing middleware for FastAPI.")
    print("\nIntegration example:")
    print(INTEGRATION_EXAMPLE)
