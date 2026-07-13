FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app

# Render supplies $PORT. Migrations run on boot so a deploy never lands on a stale schema.
CMD alembic upgrade head && \
    uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8000}
