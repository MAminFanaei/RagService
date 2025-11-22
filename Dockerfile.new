# ============ Builder stage ============
FROM python:3.10.19-slim AS builder

# ← Fixed: proper multi-line ENV with backslashes
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VERSION=1.8.3 \
    POETRY_HOME="/opt/poetry" \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1 \
    POETRY_CACHE_DIR="/tmp/poetry_cache"

# Install build dependencies + Poetry
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        libmysqlclient-dev \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -sSL https://install.python-poetry.org | python3 - \
    && ln -s /opt/poetry/bin/poetry /usr/local/bin/poetry

WORKDIR /app

# Copy only dependency definition files → cached until you actually change dependencies
COPY pyproject.toml poetry.lock ./

# Install dependencies (this layer is heavily cached)
RUN poetry install --only main --no-ansi && \
    rm -rf $POETRY_CACHE_DIR

# Now copy the actual source code (.dockerignore is respected automatically)
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .

# Install the project itself (very small layer)
RUN poetry install --only-root --no-ansi

# Create folders your app expects
RUN mkdir -p models docs


# ============ Final runtime stage ============
FROM python:3.10.19-slim

WORKDIR /app

# Copy virtualenv + installed packages
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app /app

# Activate the virtualenv for all RUN/CMD/ENTRYPOINT
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Only the runtime library needed by mysqlclient
RUN apt-get update && \
    apt-get install -y --no-install-recommends libmysqlclient21 && \
    rm -rf /var/lib/apt/lists/* && \
    apt-get clean

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Alembic migrations + your exact local Gunicorn command
CMD ["sh", "-c", "alembic -c alembic.ini upgrade head && \
    gunicorn -k uvicorn.workers.UvicornWorker -w 1 app.main:app \
        --bind 0.0.0.0:8000 \
        --timeout 60 \
        --keep-alive 5"]