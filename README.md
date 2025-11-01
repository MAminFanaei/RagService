# RAG Service - Production Ready

A production-grade RESTful API service for RAG (Retrieval-Augmented Generation) with FastAPI, featuring multi-auth, admin panel, and rate limiting.

## Features

✅ **Multi-Authentication**
- Username/Email + Password
- Google OAuth
- GitHub OAuth

✅ **Admin Panel**
- User management with custom rate limits
- View all user conversations
- RAG engine monitoring
- System statistics

✅ **Rate Limiting & Quotas**
- Per-minute rate limiting (configurable per user)
- Daily message quotas
- Redis-backed rate limiting

✅ **Chat Management**
- Create, update, delete chats
- Soft delete (all chats saved permanently)
- Auto-generate chat titles
- Full message history

✅ **RAG Pipeline**
- Async query processing with `run_in_executor`
- SSE support ready (room for implementation)
- Hybrid retrieval (Elasticsearch + BM25)
- Optional reranking

✅ **Infrastructure**
- MySQL for relational data
- Redis for caching & rate limiting
- Elasticsearch for vector search
- Docker Compose for one-command setup

## Quick Start

### 1. Prerequisites

- Docker & Docker Compose
- Python 3.10+ (for local development)

### 2. Setup

```bash
# Clone the repository
git clone <your-repo>
cd rag-service

# Copy environment file
cp .env.example .env

# Edit .env with your API keys and secrets
nano .env
```

**CRITICAL: Update these in .env:**
```bash
SECRET_KEY=<generate-strong-random-32-char-key>
ADMIN_PASSWORD=<your-secure-admin-password>
GEMINI_API_KEY=<your-gemini-api-key>

# Optional OAuth (leave empty to disable)
# See OAUTH_SETUP.md for detailed instructions
GOOGLE_CLIENT_ID=<your-google-client-id>
GOOGLE_CLIENT_SECRET=<your-google-secret>
GITHUB_CLIENT_ID=<your-github-client-id>
GITHUB_CLIENT_SECRET=<your-github-secret>
```

**📚 For OAuth setup instructions**: See [OAUTH_SETUP.md](./OAUTH_SETUP.md)

### 3. Start Services

```bash
# Start all services (MySQL, Redis, Elasticsearch, API)
docker-compose up -d

# Check logs
docker-compose logs -f app

# Wait ~30 seconds for Elasticsearch to be healthy
```

### 4. Access

- **API**: http://localhost:8000
- **Docs**: http://localhost:8000/docs
- **Health**: http://localhost:8000/health

### 5. Test Admin Login

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "login": "admin@example.com",
    "password": "your-admin-password"
  }'
```

## API Endpoints

### Authentication
```
POST   /api/v1/auth/register          - Register with email/password
POST   /api/v1/auth/login             - Login
POST   /api/v1/auth/refresh           - Refresh access token
GET    /api/v1/auth/me                - Get current user
GET    /api/v1/auth/google/login      - Google OAuth
GET    /api/v1/auth/github/login      - GitHub OAuth
```

### Chats
```
POST   /api/v1/chats                  - Create new chat
GET    /api/v1/chats                  - List user's chats
GET    /api/v1/chats/{id}             - Get chat with messages
PATCH  /api/v1/chats/{id}             - Update chat title
DELETE /api/v1/chats/{id}             - Soft delete chat
POST   /api/v1/chats/{id}/restore     - Restore deleted chat
POST   /api/v1/chats/{id}/messages    - Send message & get RAG response
```

### Admin (Requires admin role)
```
GET    /api/v1/admin/stats/system     - System statistics
GET    /api/v1/admin/stats/rag        - RAG engine stats
GET    /api/v1/admin/users            - List all users
GET    /api/v1/admin/users/{id}       - Get user details
PATCH  /api/v1/admin/users/{id}       - Update user settings
GET    /api/v1/admin/users/{id}/conversations - Export user chats
GET    /api/v1/admin/conversations/{id} - Export specific conversation
```

## Development

### Local Setup (without Docker)

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Start MySQL, Redis, Elasticsearch separately
# Then update .env with local connection strings

# Run migrations
alembic upgrade head

# Start server
uvicorn app.main:app --reload --port 8000
```

### Create Migration

```bash
# After modifying models
alembic revision --autogenerate -m "Description of changes"

# Apply migration
alembic upgrade head
```

### Database Migrations

```bash
# Create initial migration
docker-compose exec app alembic revision --autogenerate -m "initial migration"

# Apply migrations
docker-compose exec app alembic upgrade head

# Rollback
docker-compose exec app alembic downgrade -1
```

## Configuration

### User Rate Limits (Admin)

Set custom limits per user:

```python
PATCH /api/v1/admin/users/{user_id}
{
  "max_messages_per_day": 1000,      # Default: 500
  "rate_limit_per_minute": 50        # Default: 100
}
```

### RAG Configuration

Edit `.env`:

```bash
EMBEDDING_MODEL_NAME=Alibaba-NLP/gte-multilingual-base
CHUNK_TOKENS=900
ENHANCER_MAX_TOKEN=600
ANSWER_LLM_MAX_TOKEN=2000
```

**Note:** RAG config changes require service restart.

## Production Deployment

### Security Checklist

- [ ] Change `SECRET_KEY` to strong random value
- [ ] Change `ADMIN_PASSWORD`
- [ ] Set `DEBUG=false`
- [ ] Use HTTPS (add reverse proxy like Nginx)
- [ ] Set strong MySQL passwords
- [ ] Enable Elasticsearch security
- [ ] Configure CORS for production frontend
- [ ] Set up monitoring (Prometheus/Grafana)
- [ ] Configure log aggregation
- [ ] Enable database backups

### Docker Production

```bash
# Build production image
docker-compose -f docker-compose.prod.yml up -d

# Scale workers
docker-compose -f docker-compose.prod.yml up -d --scale app=4
```

## Monitoring

### Health Check

```bash
curl http://localhost:8000/health
```

### Logs

```bash
# Application logs
docker-compose logs -f app

# Database logs
docker-compose logs -f mysql

# All logs
docker-compose logs -f
```

## Troubleshooting

### Elasticsearch not healthy

```bash
# Check Elasticsearch logs
docker-compose logs elasticsearch

# Wait longer (it takes ~30 seconds to start)
curl -u elastic:ragflow_test http://localhost:9200/_cluster/health
```

### RAG engine fails to initialize

1. Check if models are downloaded in `./models/`
2. Verify `GEMINI_API_KEY` is set
3. Check proxy settings if in restricted environment

### Rate limit errors

- Check Redis connection: `docker-compose logs redis`
- Rate limits reset after 1 minute / 24 hours

## License

MIT

## Support

For issues: Create GitHub issue
For questions: admin@example.com