# Base image with Python and system dependencies
# FROM python:3.12-slim
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install system dependencies
# RUN apt-get update 
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
COPY .env .
COPY ./docs .
COPY ./models .

ENV WEB_CONCURRENCY=4

# Expose FastAPI port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["sh", "-c", "alembic upgrade head && exec gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-4} -b 0.0.0.0:8000 --worker-connections 500 --timeout 30 --keep-alive 2 --log-level info --access-logfile -"]
# # Run migrations and start server
# CMD ["sh", "-c", "alembic -c /app/alembic.ini upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]