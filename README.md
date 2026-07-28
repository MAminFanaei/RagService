# RagService — Production‑ready Retrieval‑Augmented Generation API

A production‑minded RAG backend that turns your documents into grounded, cited answers. Built for reliability, observability, and real‑world ops: auth, rate‑limits, wallets/credits, payment integrations, and a hardened multi‑turn chat with memory.


Highlights
- End‑to‑end RAG: Elasticsearch vector search (BAAI/bge‑m3, 1024‑dim), optional BM25 fallback, LLM‑based query enhancement, citation‑style answers
- Real production concerns: structured logging, timeouts/retries, per‑user rate‑limits and quotas, wallet + payment flows, soft‑delete + retention policy
- Strong boundaries: async FastAPI, SQLAlchemy + Alembic, Redis for state/limits/locks, Elasticsearch for retrieval
- Multilingual‑friendly: tuned for Persian and English with multilingual embeddings
- Test coverage: ~600 tests across endpoints, payments, credits, async behavior, edge cases (kept private for now, all passing)
- Deployment‑ready: Docker Compose with health checks and on‑boot migrations, reverse‑proxied by Nginx
- Feature‑proofing: pluggable vector DB/embeddings/LLM via config; GPU‑ready pipeline; strict config and sane defaults

Who is it for?
- Teams and individuals who want a practical, reliable, and secure document‑grounded assistant
- Backends that need multi‑turn memory, strict rate‑limits, and real payment/credit enforcement out of the box

In‑progress feature branches (nearly done)
- auto‑backup: scheduled Elasticsearch snapshots + MySQL/Redis backups and one‑command restore
- ingestion‑pipeline: structured loaders, chunking, incremental updates, and namespace support

Architecture
- FastAPI (async) application with routers for auth, chats, payments/credits, and admin
- RAG engine: embeddings → vector store → retriever (+ optional reranker) → generator
- Data: MySQL (async driver), Alembic migrations; Redis for caching/OTP/rate‑limits/locks; Elasticsearch as vector store
- External integrations: payment gateway,  SMS OTP, OAuth (Google/GitHub)

Tech stack
- Python 3.10, FastAPI, Uvicorn/Gunicorn
- SQLAlchemy 2.x (async) + Alembic, MySQL 8.0.39
- Redis/Valkey
- Elasticsearch 9.x (vectors)
- LangChain + langchain‑huggingface; Embeddings: BAAI/bge‑m3 (1024‑dim); LLMs via provider APIs (Gemini, OpenAI, DeepSeek, etc.)
- structlog, prometheus‑client
- Docker Compose + Nginx

RAG pipeline details
- Embeddings: BAAI/bge‑m3 (1024‑dim)
- Vector store: Elasticsearch HNSW; configurable top_k and filters
- Optional BM25 fallback (keyword)
- Query enhancement: LLM expands/clarifies user input using recent memory window
- Generation: LLM composes answer with citations; memory window is configurable
- Memory & costs: per‑message credit debit, free tier for new users
- GPU: ready (PyTorch/Transformers); can be disabled on CPU‑only hosts

Data & persistence
- MySQL 8: users, chat_sessions, messages, message_credits, payments, wallets, discount_codes
- Soft‑delete and retention: personally identifiable chat data is cleaned after 30 days of user deletion; payment records retained (legal)
- Redis: OTPs, rate‑limits, locks, and transient state

Performance (local measurements)
- Hardware: i5 CPU, GTX 1070, 16 GB RAM
- Retrieval: ~300 ms per query (BAAI/bge‑m3) with ~50 books (~200 pages each)
- Concurrency: ~30 retrievals/sec on the above machine
- Cost: ~0.005 USD per answer using Gemeni flash (depends on provider/model)
- Load testing: k6 scripts present (login + message flows).

Evaluation & testing
- ~600 tests across endpoints, payments, credits, rate‑limits, async behavior, and failure paths -- all passing
- Load/diagnostic scripts: k6, concurrency tests, password hashing parallelism, full system health checks
- Answer quality: currently human‑evaluated but embeddings/models/prompts tuned carefully for reliability with Persian + English
- Future Plans i have: automated eval suite (faithfulness, context recall), regression gates in CI

Security & privacy
- JWT auth; bcrypt + Argon2 password hashing
- OAuth (Google/GitHub), OTP via SMS (Melipayamak)
- Per‑user rate‑limits and daily quotas
- Double‑spend protection and reconciliation for payments (Redis locks)
- TLS via reverse proxy (Nginx) in production
- Data retention: GDPR‑style cleanup for deleted users; payment data retained
- Future Plans i have: column‑level encryption for PII, audit logging, formal security checklist and CI scanners

Operations & deployment
- Docker Compose with health checks across app, MySQL, Redis, Elasticsearch, Nginx
- Alembic migrations on boot; structured logs via structlog
- Prometheus client included; expose metrics endpoint if enabled
- Scale up: increase Gunicorn/Uvicorn workers (CPU‑bounded), offload embeddings to GPU
- Current deployment: single host (tested on VPS); Kubernetes planned

The Roadmap i have in mind 
- Finilizing the new branches: auto‑backup (ES snapshots + DB/Redis), ingestion‑pipeline (incremental + namespace)
- Adding SSE/streaming responses
- Creating a Admin dashboard: user, payments, and document management
- Using Kubernetes ; autoscaling; distributed tracing
- Column‑level PII encryption
- Microservice split (payments already ~98% isolated; retrieval/GPU next)

License
- MIT (recommended) — simple, permissive. Or Apache‑2.0 if you want an explicit patent grant.

Maintainer
- Mohammad Amin Fanaei
- Contact: mohammadaminfanaie@gmail.com / ir.linkedin.com/in/mohammad-amin-fanaei-71b3542a4
- Frontend (Mr naser safari): github.com/nsafari/chat-bot

Quickstart

Docker (recommended)
- Prereqs: Docker + Docker Compose
- Steps:
  1) git clone https://github.com/MAminFanaei/RagService && cd RagService
  2) cp .env.example .env and fill required secrets (see Configuration)
  4) adding the system prompt for both generator and enhancer llm (check out prompts.example.py)
  3) docker compose up -d --build
  5) Migrations (if not auto‑run): docker exec ragservice-app alembic upgrade head
  6) API at http://localhost:8000, docs at http://localhost:8000/docs

Local Python (dev only)
- pip install -r requirements.txt
- export env vars (or use .env)
- uvicorn app.main:app --reload

Configuration (env vars)

Must set
- SECRET_KEY
- Database: MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE, DATABASE_URL
- Redis: REDIS_URL
- Elasticsearch: ELASTICSEARCH_USERNAME, ELASTICSEARCH_PASSWORD, ELASTICSEARCH_INDEX_NAME
- LLM: LLM_API_KEY, QUERY_ENHANCER_MODEL_NAME, ANSWER_GENERATOR_MODEL_NAME
- Payment (for Iran): SEP_TERMINAL_ID, SEP_* URLs, PAYMENT_CALLBACK_URL
- SMS (OTP): MELIPAYAMAK_USERNAME, MELIPAYAMAK_PASSWORD
- Embedder : EMBEDDING_MODEL_PATH
