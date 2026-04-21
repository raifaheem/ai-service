# ---- Stage 1: build deps into an isolated prefix ----
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Stage 2: slim runtime ----
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/usr/local/bin:${PATH}"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
COPY app ./app

RUN groupadd -r appgroup && useradd -r -g appgroup appuser \
    && chown -R appuser:appgroup /app

# prometheus_client multiprocess mode requires a writable dir that all gunicorn
# workers share and that's wiped on container restart. /tmp/prom_multiproc is
# that dir; each worker writes per-pid shard files there which the /metrics
# handler aggregates on scrape.
ENV PROMETHEUS_MULTIPROC_DIR=/tmp/prom_multiproc
RUN mkdir -p /tmp/prom_multiproc && chown -R appuser:appgroup /tmp/prom_multiproc

USER appuser

EXPOSE 8001

ENV GUNICORN_WORKERS=4

# --graceful-timeout 30 matches SHUTDOWN_TIMEOUT in app/lifecycle.py so SSE streams drain cleanly.
CMD gunicorn app.main:app \
     -k uvicorn.workers.UvicornWorker \
     -w ${GUNICORN_WORKERS} \
     --bind 0.0.0.0:8001 \
     --timeout 120 \
     --graceful-timeout 30 \
     --access-logfile - \
     --error-logfile -
