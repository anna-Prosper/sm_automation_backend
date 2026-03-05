FROM python:3.11-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y \
    curl \
    fontconfig \
    fonts-dejavu-core \
    libavif-dev \
    libheif-dev \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f -v

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --default-timeout=120 --retries 10 -r requirements.txt



COPY app /app/app

COPY assets /app/assets

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

FROM base AS api

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["gunicorn", "app.main:app", "--workers", "2", "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000","--timeout", "600","--graceful-timeout", "600","--keep-alive", "10", "--access-logfile", "-", "--error-logfile", "-","--log-level", "info"]

FROM base AS worker

CMD ["celery", "-A", "app.workers.celery_app", "worker", \
     "--loglevel=info", \
     "--concurrency=2", \
     "--max-tasks-per-child=1000"]


FROM base AS beat

RUN mkdir -p /app/beat
CMD ["celery", "-A", "app.workers.celery_app", "beat", \
     "--loglevel=info", \
     "--schedule=/app/beat/celerybeat-schedule"]