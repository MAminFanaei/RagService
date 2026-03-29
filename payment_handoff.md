# Payment Service Handoff Document
# Complete Context for Continuing Development
---
## 1. PROJECT OVERVIEW
### What This Is
A **payment microservice module** built in Python/FastAPI, integrated into an existing RAG (Retrieval-Augmented Generation) service. The payment module integrates with **Saman Electronic Payment (SEP)** — an Iranian Payment Service Provider (PSP) that uses a **token → redirect → callback → verify** flow.
### Architecture Decision
The payment module is **NOT** a separate microservice. It's a **FastAPI router package** inside the existing app, mounted via `app.include_router()`. This was chosen because:
- It shares the same DB engine, Redis client, auth system, and exception handlers
- Zero duplication
- Can be extracted to a separate service later if needed
### Integration Point in main.py
```python
from app.payment.routers import payment_router
app.include_router(payment_router, prefix="/api/v1/payment", tags=["Payment"])
```
---
## 2. EXISTING RAG SERVICE STACK
### Tech Stack
- **Framework:** FastAPI (async)
- **ORM:** SQLAlchemy 2.0 (fully async with `AsyncSession`)
- **Database:** MySQL (must also be compatible with PostgreSQL)
- **Cache/Lock:** Redis (via `redis.asyncio`)
- **Migrations:** Alembic
- **Auth:** JWT Bearer tokens
- **Logging:** structlog (`logger = structlog.get_logger()`)
- **Config:** pydantic-settings reading from `.env` file
- **Testing:** pytest
- **Containerization:** Docker + docker-compose
### Key Existing Files
```
app/
├── __init__.py
├── main.py                    # FastAPI app entry, includes payment_router
├── config.py                  # Settings class (extra="ignore" — IMPORTANT)
├── exceptions.py              # AppException hierarchy (payment extends this)
├── core/
│   ├── database.py            # get_db(), get_redis(), Base, AsyncSession setup
│   └── security.py            # JWT decode
├── api/
│   └── deps.py                # get_current_user(), get_current_admin_user()
├── models/
│   └── user.py                # User model (id=String(36), UUID)
├── middleware/
│   ├── error_handler.py       # Global exception handler
│   └── process_timing.py
```
### Critical Shared Dependencies
```python
# Database session — payment uses this exact dependency
from app.core.database import get_db  # Returns AsyncSession
from app.core.database import get_redis  # Returns aioredis.Redis
# Auth — payment endpoints use these
from app.api.deps import get_current_user  # Returns User object
from app.api.deps import get_current_admin_user  # Returns admin User
# Base model class — all payment models inherit from this
from app.core.database import Base  # SQLAlchemy declarative base
# Exceptions — payment exceptions extend these
from app.exceptions import AppException, BadRequestException, NotFoundException, ConflictException
```
### User Model (referenced by payment via FK)
```python
class User(Base):
    __tablename__ = "users"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String(50), unique=True, nullable=True, index=True)
    email = Column(String(255), unique=True, nullable=True, index=True)
    hashed_password = Column(String(255), nullable=True)
    auth_provider = Column(Enum(AuthProvider), default=AuthProvider.LOCAL)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
```
### Settings Compatibility
The main `Settings` class in `app/config.py` uses `extra="ignore"` so it skips payment-specific env vars like `SEP_TERMINAL_ID`. This was changed from `extra="forbid"` to prevent conflicts.
### Redis Connection
```python
# In app/core/database.py
import redis.asyncio as aioredis
_redis_client: aioredis.Redis = None
async def get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = await aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=settings.REDIS_MAX_CONNECTIONS
        )
    return _redis_client
```
---
## 3. PAYMENT MODULE STRUCTURE
```
app/payment/
├── __init__.py                 # Package docstring
├── config.py                   # PaymentSettings (pydantic-settings)
├── exceptions.py               # Payment-specific exceptions (extend AppException)
├── core/
│   ├── __init__.py
│   ├── constants.py            # Enums, SEP status codes, business rules
│   ├── locker.py               # Redis distributed lock (DistributedLocker + acquire_lock)
│   └── metrics.py              # Prometheus metrics (PaymentMetrics)
├── models/
│   ├── __init__.py             # Imports all models for Alembic detection
│   ├── payment.py              # Payment model (27 columns)
│   ├── reverse.py              # Reverse model
│   ├── wallet.py               # Wallet + WalletTransaction models
│   └── discount.py             # DiscountCode + DiscountUsage models
├── schemas/
│   ├── __init__.py
│   ├── payment.py              # Request/response Pydantic models
│   ├── reverse.py
│   ├── wallet.py
│   └── discount.py
├── services/
│   ├── __init__.py
│   ├── sep_client.py           # HTTP client for SEP APIs (Token, Verify, Reverse)
│   ├── payment_service.py      # Core payment business logic
│   ├── reverse_service.py      # Reverse transaction logic
│   ├── wallet_service.py       # Wallet credit/debit/balance
│   ├── discount_service.py     # Discount code validation & application
│   └── double_spend_guard.py   # RefNum deduplication
├── routers/
│   ├── __init__.py             # Combines all routers into payment_router
│   ├── health.py               # GET /health, GET /readiness
│   ├── initiate.py             # POST /initiate
│   ├── callback.py             # POST /callback (SEP redirects here)
│   ├── reverse.py              # POST /{id}/reverse
│   ├── query.py                # GET /{id}, GET /list
│   ├── wallet.py               # GET /wallet/balance, GET /wallet/transactions
│   ├── discount.py             # POST /discount/create, POST /discount/validate
│   └── metrics.py              # GET /metrics (Prometheus)
```
---
## 4. SEP (SAMAN ELECTRONIC PAYMENT) INTEGRATION
### How SEP Works (Token + Redirect Flow)
1. **Merchant gets Token** — POST to SEP with amount, terminal ID, etc. → get a token
2. **Redirect buyer** — Send buyer's browser to SEP's payment page with the token
3. **Buyer pays** — Enters card info on SEP's page (we NEVER touch card data)
4. **SEP calls back** — Redirects buyer's browser to our callback URL with POST form data
5. **Merchant verifies** — Call VerifyTransaction API to confirm payment
6. **Done** — Credit wallet, show result
### SEP API Endpoints
```
Token:    POST https://sep.shaparak.ir/OnlinePG/OnlinePG
Redirect: GET  https://sep.shaparak.ir/OnlinePG/SendToken?token=xxx
Verify:   POST https://sep.shaparak.ir/verifyTxnRandomSessionkey/ipg/VerifyTransaction
Reverse:  POST https://sep.shaparak.ir/verifyTxnRandomSessionkey/ipg/ReverseTransaction
```
### CRITICAL: Two Different "Code 2" Systems
This caused confusion during development. There are TWO completely different code systems:
**System 1: Callback Status (from SEP redirect POST to our callback URL)**
| Status | State | Meaning |
|--------|-------|---------|
| 1 | CanceledByUser | User cancelled |
| **2** | **OK** | **Payment successful — money left the card** |
| 3 | Failed | Payment failed |
| 4 | SessionIsNull | Timeout |
| 5 | InvalidParameters | Bad params |
**System 2: Verify/Reverse ResultCode (from API response)**
| ResultCode | Meaning |
|------------|---------|
| **0** | Success — verify/reverse completed |
| **2** | Duplicate request — already processed |
| -2 | Transaction not found |
| -6 | More than 30 min passed |
| 5 | Transaction already reversed |
| -104 | Terminal inactive |
| -105 | Terminal not found |
| -106 | IP not authorized |
### SEP Verify/Reverse Request Format
```json
POST https://sep.shaparak.ir/verifyTxnRandomSessionkey/ipg/VerifyTransaction
{
    "RefNum": "string",
    "TerminalNumber": 2015  // INTEGER, not string!
}
```
**CRITICAL QUIRK:** Token API uses `TerminalId` (string). Verify/Reverse use `TerminalNumber` (integer). Different name, different type.
### SEP Verify Response Format
```json
{
    "TransactionDetail": {
        "RRN": "14226761817",
        "RefNum": "50",
        "MaskedPan": "621986****8080",
        "HashedPan": "b96a14...",
        "TerminalNumber": 2001,
        "OrginalAmount": 1000,    // NOTE: SEP's typo — "Orginal" not "Original"
        "AffectiveAmount": 1000,
        "StraceDate": "2019-09-16 18:11:06",
        "StraceNo": "100428"
    },
    "ResultCode": 0,
    "ResultDescription": "عملیات با موفقیت انجام شد",
    "Success": true
}
```
### SEP Token Request Format
```json
POST https://sep.shaparak.ir/onlinepg/onlinepg
{
    "action": "token",
    "TerminalId": "0000",
    "Amount": 12000,
    "ResNum": "unique-order-id",
    "RedirectUrl": "https://yourserver.com/api/v1/payment/callback",
    "CellNumber": "9120000000"
}
```
### SEP Token Response
```json
// Success:
{"status": 1, "token": "2c3c1fefac5a48geb9f9be7e445dd9b2"}
// Error:
{"status": -1, "errorCode": "5", "errorDesc": "پارامترهای ارسالی نامعتبر است"}
```
### SEP Callback POST Parameters (sent to our RedirectURL)
```
MID, State, Status, RRN, RefNum, ResNum, TerminalId, TraceNo,
Amount, Wage, SecurePan, HashedCardNumber
```
### Business Rules from SEP Docs
- Token expires in 20 min (configurable 20-3600 min)
- Must call VerifyTransaction within **30 minutes** or SEP auto-reverses
- Can call ReverseTransaction within **50 minutes** of transaction
- Verify can be called multiple times — SEP confirms each time
- **Double-spending prevention is MERCHANT's responsibility** — SEP will verify same RefNum repeatedly
- If Verify response doesn't arrive (timeout), RETRY — but only retry on no-response, not on error response
- Amount check: verified amount MUST equal expected amount (Case A=success, Case B=mismatch→reverse, Case C=error)
- Merchant must NOT handle/store card data — SEP handles all card info
- Case sensitivity matters for parameter names
---
## 5. DATABASE SCHEMA (6 New Tables)
### payments (27 columns)
```sql
id              VARCHAR(36) PK
user_id         VARCHAR(36) FK → users.id
res_num         VARCHAR(50) UNIQUE        -- Our order number
ref_num         VARCHAR(50) UNIQUE NULL   -- SEP's digital receipt
amount          BIGINT                     -- Final amount (after discount) in Rials
original_amount BIGINT
discount_code_id VARCHAR(36) FK NULL
discount_amount BIGINT DEFAULT 0
terminal_id     VARCHAR(20)
token           VARCHAR(100) NULL
state           VARCHAR(20) NULL           -- SEP State (OK, Failed, etc.)
status_code     INTEGER NULL               -- SEP numeric Status
status          VARCHAR(20)                -- Our internal status enum
rrn             VARCHAR(50) NULL
trace_no        VARCHAR(50) NULL
secure_pan      VARCHAR(30) NULL
hashed_card_number VARCHAR(100) NULL
verified_amount BIGINT NULL
failure_reason  TEXT NULL
sep_result_code INTEGER NULL
sep_result_description TEXT NULL
description     VARCHAR(255) NULL
created_at      DATETIME(tz)
updated_at      DATETIME(tz)
callback_at     DATETIME(tz) NULL
verified_at     DATETIME(tz) NULL
```
### PaymentStatus Enum Values
```
PENDING → TOKEN_OBTAINED → CALLBACK_RECEIVED → VERIFIED
                                              → FAILED
                                              → AMOUNT_MISMATCH
                                              → VERIFY_TIMEOUT
                                              → REVERSED
```
### wallets
```sql
id         VARCHAR(36) PK
user_id    VARCHAR(36) FK → users.id, UNIQUE
balance    BIGINT
created_at DATETIME(tz)
updated_at DATETIME(tz)
```
### wallet_transactions
```sql
id            VARCHAR(36) PK
wallet_id     VARCHAR(36) FK → wallets.id
payment_id    VARCHAR(36) FK → payments.id NULL
amount        BIGINT          -- Positive=credit, Negative=debit
balance_after BIGINT
tx_type       VARCHAR(10)     -- CREDIT or DEBIT
description   VARCHAR(255)
created_at    DATETIME(tz)
```
### reverses
```sql
id                 VARCHAR(36) PK
payment_id         VARCHAR(36) FK → payments.id
ref_num            VARCHAR(50)
reason             TEXT
status             VARCHAR(20)    -- PENDING, COMPLETED, FAILED
result_code        INTEGER NULL
result_description TEXT NULL
created_at         DATETIME(tz)
updated_at         DATETIME(tz)
```
### discount_codes
```sql
id             VARCHAR(36) PK
code           VARCHAR(50) UNIQUE
discount_type  VARCHAR(10)     -- PERCENTAGE or FIXED
discount_value BIGINT          -- Percent (1-100) or fixed Rials
max_discount   BIGINT NULL     -- Cap for percentage
min_purchase   BIGINT DEFAULT 0
max_uses       INTEGER NULL    -- NULL=unlimited
used_count     INTEGER DEFAULT 0
per_user_limit INTEGER DEFAULT 1
valid_from     DATETIME(tz)
valid_until    DATETIME(tz) NULL
is_active      BOOLEAN DEFAULT TRUE
description    VARCHAR(255) NULL
created_at     DATETIME(tz)
```
### discount_usages
```sql
id               VARCHAR(36) PK
discount_code_id VARCHAR(36) FK → discount_codes.id
user_id          VARCHAR(36) FK → users.id
payment_id       VARCHAR(36) FK → payments.id
discount_amount  BIGINT
used_at          DATETIME(tz)
```
---
## 6. API ENDPOINTS
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/payment/health` | None | Health check |
| GET | `/api/v1/payment/readiness` | None | Readiness probe |
| GET | `/api/v1/payment/metrics` | None (needs securing) | Prometheus metrics |
| POST | `/api/v1/payment/initiate` | JWT | Start payment → get token + redirect URL |
| POST | `/api/v1/payment/callback` | None | SEP redirects buyer here → auto-verify → credit wallet |
| GET | `/api/v1/payment/{payment_id}` | JWT | Get payment details |
| GET | `/api/v1/payment/list` | JWT | List user's payments (paginated, filtered) |
| POST | `/api/v1/payment/{payment_id}/reverse` | JWT | Reverse a verified payment |
| GET | `/api/v1/payment/{payment_id}/reverses` | JWT | List reverses for a payment |
| GET | `/api/v1/payment/wallet/balance` | JWT | Get wallet balance |
| GET | `/api/v1/payment/wallet/transactions` | JWT | Wallet transaction history |
| POST | `/api/v1/payment/discount/create` | Admin JWT | Create discount code |
| POST | `/api/v1/payment/discount/validate` | JWT | Validate discount code |
---
## 7. COMPLETE PAYMENT FLOW
```
STEP 1: Frontend → POST /api/v1/payment/initiate
        Body: { "amount": 500000, "discount_code": "WELCOME20" }
        Auth: Bearer JWT
STEP 2: Service validates amount, applies discount, generates ResNum,
        calls SEP Token API
        Returns: { payment_id, token, redirect_url, amount, discount_applied }
STEP 3: Frontend redirects user's browser to:
        https://sep.shaparak.ir/OnlinePG/SendToken?token=xxx
STEP 4: User enters card info on SEP's page (we never see card data)
STEP 5: SEP processes payment, redirects user's browser via POST to:
        https://yourserver.com/api/v1/payment/callback
        With: State, RefNum, ResNum, Amount, RRN, TraceNo, SecurePan, etc.
STEP 6: Callback endpoint automatically:
        a) Parses form data
        b) Finds payment by ResNum
        c) Checks State == "OK" and Status == 2
        d) Checks RefNum not already used (double-spend guard)
        e) Acquires Redis lock on RefNum
        f) Calls SEP VerifyTransaction API (with retries on timeout)
        g) Checks verified amount == expected amount
        h) Credits user's wallet
        i) Updates payment record to VERIFIED
        j) Releases lock
        k) 302 redirects user's browser to:
           https://yourfrontend.com/payment/result?status=VERIFIED&payment_id=xxx&amount=500000
STEP 7: Frontend reads URL params and shows result to user
```
---
## 8. DOUBLE-SPENDING PREVENTION (3 Layers)
```
Layer 1: DATABASE
  └─ ref_num column has UNIQUE constraint
  └─ INSERT fails if same RefNum exists → impossible to store twice
Layer 2: REDIS LOCK
  └─ Before processing callback, acquire lock: "lock:payment:callback:{ref_num}"
  └─ TTL = 5 minutes
  └─ If lock exists → another worker processing same callback
Layer 3: APPLICATION CHECK (DoubleSpendGuard)
  └─ Before calling Verify, SELECT payment WHERE ref_num = X AND status = VERIFIED
  └─ If found → already verified → return existing result, don't credit again
```
---
## 9. ENVIRONMENT VARIABLES (.env additions)
```env
# SEP Configuration
SEP_TERMINAL_ID=0000
SEP_PAYMENT_URL=https://sep.shaparak.ir/OnlinePG/OnlinePG
SEP_VERIFY_URL=https://sep.shaparak.ir/verifyTxnRandomSessionkey/ipg/VerifyTransaction
SEP_REVERSE_URL=https://sep.shaparak.ir/verifyTxnRandomSessionkey/ipg/ReverseTransaction
SEP_TOKEN_EXPIRY_MIN=20
SEP_HTTP_TIMEOUT=30
# Callback & Frontend
PAYMENT_CALLBACK_URL=https://yourserver.com/api/v1/payment/callback
FRONTEND_PAYMENT_RESULT_URL=https://yourfrontend.com/payment/result
# Business Rules
MIN_PAYMENT_AMOUNT=10000
MAX_PAYMENT_AMOUNT=500000000
PAYMENT_REVERSE_WINDOW_MINUTES=45
# Locks
PAYMENT_LOCK_TIMEOUT=30
PAYMENT_LOCK_TTL=300
# Verify Retry
PAYMENT_VERIFY_MAX_RETRIES=3
PAYMENT_VERIFY_RETRY_DELAY=2
```
---
## 10. KEY DESIGN DECISIONS & RATIONALE
### Currency: Rials
Backend works in Rials. Frontend converts to Tomans if needed.
### Wallet System
- Separate `wallets` table (not a column on User) — zero changes to User model
- Ledger-based: every change creates a `WalletTransaction` record
- Atomic balance updates: `UPDATE wallets SET balance = balance + X` (SQL-level, prevents race conditions)
- Lazy initialization: wallet created on first access
### Discount System
- One discount code per transaction (no stacking)
- Two types: PERCENTAGE (with optional cap) and FIXED
- Per-user usage limits
- Validity windows (valid_from → valid_until)
- Discount applied BEFORE sending amount to SEP (user pays less)
### Metrics: Option E (Admin JWT + API Key fallback)
Decision was made but NOT yet implemented. Currently metrics endpoint is open.
Plan: Try JWT first, if no JWT check `X-Metrics-Key` header for Prometheus scraper.
### Locking: Redis Distributed Lock
- Uses SET NX EX (atomic set-if-not-exists with TTL)
- Lua script for safe unlock (atomic check-owner-then-delete)
- Exponential backoff retry
- Key prefixes: `lock:payment:ref:`, `lock:reverse:`, `lock:wallet:`, `lock:payment:callback:`
---
## 11. BUGS FOUND AND FIXED DURING DEVELOPMENT
### Fixed ✅
| Bug | Root Cause | Fix |
|-----|-----------|-----|
| `PaymentService` not found | Class was named `PaymentProcessingService` | Renamed to `PaymentService` |
| `LockPrefix` not found | Missing from constants.py | Added `LockPrefix` class |
| Settings crash on payment env vars | Main Settings had `extra="forbid"` | Changed to `extra="ignore"` |
| `WalletTransactionType` not found | Constants used `WalletTxType` | Added alias `WalletTransactionType = WalletTxType` |
| `DiscountCodeInvalidException` not found | Named `InvalidDiscountCodeException` | Added alias |
| `acquire_lock` not found | Only had `DistributedLocker` class | Added standalone `acquire_lock()` function |
| Different wallet_id every request | Wallet created with `flush()` but never `commit()` | Added `await db.commit()` in wallet router |
| DiscountCode missing `payments` relationship | Payment had `back_populates="payments"` but DiscountCode had no `payments` rel | Added relationship |
| DiscountUsage missing `payment` relationship | Same issue | Added `payment` and `user` relationships |
| `sep_client.py` missing on disk | User didn't copy Chunk 5 | Provided the file |
| `description` column missing on Payment | Service set it but model didn't have it | Added column |
| `description` column missing on DiscountCode | Same issue | Added column |
| Metrics router not included | Not in `routers/__init__.py` | Added import and include |
| Health router missing | File never created | Created `routers/health.py` |
| Circular import on verify script | `from app.core.database import Base` triggered circular | Import redis directly |
| PaymentSettings wrong env_file path | Looking for `.env` in `app/payment/` | Removed `env_file`, relies on parent |
## 12. HOW THINGS CONNECT (Import Map)
```
routers/initiate.py
  → services/payment_service.py (PaymentService)
    → services/sep_client.py (sep_client.request_token, build_redirect_url)
    → services/discount_service.py (DiscountService.validate_and_calculate)
    → core/metrics.py (metrics.payment_initiated)
    → core/locker.py (acquire_lock)
    → models/payment.py (Payment)
routers/callback.py
  → services/payment_service.py (PaymentService.process_callback)
    → services/sep_client.py (sep_client.verify_transaction, CallbackData)
    → services/wallet_service.py (WalletService.credit)
    → services/discount_service.py (DiscountService.record_usage)
    → services/double_spend_guard.py (DoubleSpendGuard)
    → core/locker.py (acquire_lock)
    → core/metrics.py (metrics.payment_verified, etc.)
routers/reverse.py
  → services/reverse_service.py (ReverseService)
    → services/sep_client.py (sep_client.reverse_transaction)
    → services/wallet_service.py (WalletService.debit)
    → models/reverse.py (Reverse)
routers/wallet.py
  → services/wallet_service.py (WalletService)
    → models/wallet.py (Wallet, WalletTransaction)
routers/discount.py
  → services/discount_service.py (DiscountService)
    → models/discount.py (DiscountCode, DiscountUsage)
```
---
## 13. RUNNING THE SERVICE
```bash
# Start server
uvicorn app.main:app --reload
# Run payment tests
python3 -m pytest tests/payment/ -v
# Verify setup
python scripts/verify_payment_setup.py
# Database migration
alembic revision --autogenerate -m "description"
alembic upgrade head
# If alembic gets confused with multiple heads
alembic stamp head  # Reset to current state
```
---
## 14. TEST FILES LOCATION
Tests are in `rag-tests/payment/` with a symlink `tests -> rag-tests` for Python imports.
```
tests/payment/
├── conftest.py              # Fixtures, mock SEP, test DB setup
├── test_initiate_payment.py
├── test_callback.py
├── test_verify.py
├── test_reverse.py
├── test_discount.py
├── test_double_spending.py
├── test_query.py
├── test_metrics.py
└── test_wallet.py
```
---
## 15. WHAT NEEDS TO BE DONE NEXT
### Priority 1: Metrics Security
- Implement Option E: Admin JWT + API Key header fallback
- Add `METRICS_API_KEY` to .env
- Metrics endpoint checks JWT first, then X-Metrics-Key header
### Priority 2: Production Readiness
- Get real SEP terminal ID and credentials
- Whitelist server IP with SEP
- Set real PAYMENT_CALLBACK_URL and FRONTEND_PAYMENT_RESULT_URL
- Test with real card (small amount)
- Add rate limiting to payment endpoints
- Add request logging/audit trail
### Priority 3: Enhancements
- Background task to check VERIFY_TIMEOUT payments
- Admin endpoint to list uncertain/failed payments
- Webhook notifications for payment events
- Payment expiry cleanup job
---
## 16. IMPORTANT WARNINGS FOR FUTURE DEVELOPMENT
1. **NEVER handle card data** — SEP handles all card info on their page
2. **Parameter names are case-sensitive** — `TerminalId` (Token API) vs `TerminalNumber` (Verify API)
3. **SEP has a typo: `OrginalAmount`** — not `OriginalAmount`. Preserve the typo in code.
4. **`db.flush()` is NOT `db.commit()`** — flush sends SQL but doesn't persist. Always commit in routers.
5. **Callback endpoint has NO auth** — SEP calls it, not users. But validate via ResNum lookup.
6. **Double-spending is YOUR responsibility** — SEP will happily verify same RefNum multiple times
7. **30-min verify window** — if you don't verify within 30 min, SEP auto-reverses
8. **50-min reverse window** — can only reverse within 50 min of transaction
9. **All amounts in Rials** — frontend converts to Tomans
10. **`extra="ignore"` on main Settings** — MUST stay this way or payment env vars crash the app