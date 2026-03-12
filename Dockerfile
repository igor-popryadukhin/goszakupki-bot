FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libxml2-dev \
    libxslt1-dev \
    libffi-dev \
    tzdata \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml poetry.lock* /app/
RUN pip install --no-cache-dir poetry \
 && poetry config virtualenvs.create false \
 && poetry install --no-interaction --no-ansi --only main --no-root

COPY src/ /app/src/

RUN useradd -ms /bin/bash appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data /app

USER appuser

ENV DB_PATH=/data/app.db TZ=Europe/Helsinki

CMD ["python", "-m", "src.app"]
