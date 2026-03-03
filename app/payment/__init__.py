"""
Payment Service Package — SEP (Saman Electronic Payment) Integration

A self-contained payment module for wallet charging via SEP's IPG (Internet Payment Gateway).
Designed to be integrated into the main FastAPI app via include_router().

Architecture:
    - config.py        → Payment-specific settings from .env
    - core/constants.py → SEP status codes, enums, business rules
    - exceptions.py    → Payment-specific exceptions (extends AppException)
    - models/          → SQLAlchemy models (Chunk 2)
    - schemas/         → Pydantic request/response models (Chunk 3)
    - services/        → Business logic + SEP client (Chunks 5-6)
    - routers/         → FastAPI endpoints (Chunk 7)

Flow:
    1. Frontend → POST /api/v1/payment/initiate (get token + redirect URL)
    2. Frontend redirects user to SEP payment page
    3. User pays on SEP's page (we never see card data)
    4. SEP → POST /api/v1/payment/callback (auto-verify + credit wallet)
    5. Service redirects user to frontend with result
"""
