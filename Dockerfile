# Use the base image you built once
FROM myapp-base:latest

WORKDIR /app

# Only copy application code (this is the only part that changes frequently)
COPY ./app ./app
COPY ./alembic ./alembic
COPY alembic.ini .
COPY ./docs ./docs
# COPY .env .
COPY ./models ./models