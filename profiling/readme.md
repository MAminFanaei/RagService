# RAG Service Profiling Tools

## Quick Start

```bash
# Install dependencies
pip install rich

# Run the profiler
python -m profiling.profile_rag_pipeline
```

## What It Tests

| Test | What It Measures | Why It Matters |
|------|------------------|----------------|
| Password Hashing | Argon2 hash/verify time | 🔴 BLOCKS event loop if slow |
| Embedding Generation | HuggingFace model inference | CPU-bound, may block |
| Elasticsearch | Network latency to ES | Usually fast |
| MySQL | Async query performance | Should be <10ms |
| Redis | Get/Set latency | Should be <5ms |
| LLM API | External API latency | Network bound |
| Full Pipeline | End-to-end RAG query | Total user experience |
| Concurrency | Parallel request handling | Reveals serialization |

## Understanding Results

### Password Hashing (Argon2)
```
Hash avg: 500ms   ← Creating new passwords
Verify avg: 450ms ← EVERY LOGIN!
```

**If verify is >100ms, this is blocking your event loop during authentication!**

**Fix:** Use async password verification:
```python
# In user_service.py
from app.core.security_async import verify_password_async

async def authenticate(...):
    if not await verify_password_async(password, user.hashed_password):
        return None
```

### Embedding Generation
```
Single embed avg: 150ms
Device: cpu
```

**If >50ms on CPU, consider:**
- GPU acceleration (`DEVICE=cuda`)
- Embedding caching
- Smaller model

### Concurrency Test
```
Parallelism ratio: 1.5x (max: 10x)
⚠️ LOW PARALLELISM - Something is serializing requests!
```

**This means requests are NOT running in parallel.** Common causes:
- Blocking calls in async functions
- Single-threaded model inference
- Database connection contention

## Adding Timing to Your Endpoints

### 1. Add Timing Middleware

```python
# In app/main.py
from profiling.middleware_timing import TimingMiddleware

app = FastAPI(...)
app.add_middleware(TimingMiddleware)
```

### 2. Add Checkpoints in Endpoints

```python
# In app/api/v1/chats.py
from profiling.middleware_timing import ProfileBlock, add_checkpoint

@router.post("/{chat_id}/messages")
async def send_message(request: Request, ...):
    add_checkpoint(request, "start")
    
    async with ProfileBlock(request, "rate_limit"):
        # rate limit check
        pass
    
    async with ProfileBlock(request, "rag_query"):
        result = await RAGService.process_query(...)
    
    return result
```

### 3. Check Logs

```
INFO: Endpoint profile endpoint=send_message total_ms=1234.56 breakdown={'rate_limit': 5.2, 'rag_query': 1200.3}
```

## Common Bottlenecks & Fixes

### 🔴 Argon2 Password Hashing (Login: 3s+)

**Problem:** Argon2 is CPU-intensive and blocks event loop.

**Fix:** Replace `security.py` with `security_async.py`:
```python
# Change imports in user_service.py
from app.core.security_async import verify_password_async, get_password_hash_async

# Change authenticate method
async def authenticate(db, login, password):
    user = await UserService.get_by_login(db, login)
    if not user or not user.hashed_password:
        return None
    if not await verify_password_async(password, user.hashed_password):  # ← async!
        return None
    ...
```

### 🟡 Embedding Model Serialization

**Problem:** HuggingFace model can only process one query at a time.

**Fix:** Use a semaphore and batch processing:
```python
_embedding_semaphore = asyncio.Semaphore(1)  # Only 1 concurrent embedding

async def get_embedding(text):
    async with _embedding_semaphore:
        return await asyncio.get_event_loop().run_in_executor(
            None, embeddings.embed_query, text
        )
```

### 🟡 BM25 Initialization

**Problem:** BM25Retriever rebuilds index on every call.

**Fix:** Initialize once at startup (already done in your code).

## Expected Results After Fixes

| Metric | Before | After Fix |
|--------|--------|-----------|
| Login time | 3s+ | <200ms |
| 1 worker req/s | 12 | 50-100 |
| 6 workers req/s | 27 | 150-300 |
| Parallelism ratio | 1.5x | 5-8x |
