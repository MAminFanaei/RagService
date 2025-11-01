# Base image with Python and system dependencies
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    default-libmysqlclient-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements.txt
COPY requirements.txt .

# Install Python packages
RUN pip install -r requirements.txt

# Set default working directory (optional)
WORKDIR /app

# Copy the rest of the project
COPY ./app ./app
COPY ./alembic ./alembic
COPY alembic.ini .

# Create directories for models/docs if missing
RUN mkdir -p /app/models /app/docs

# Expose FastAPI port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run migrations and start server
CMD ["sh", "-c", "alembic -c /app/alembic.ini upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]