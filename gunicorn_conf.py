"""Gunicorn config: worker recycling, Prometheus multiproc hygiene, preload.

Single source of truth for production worker tuning. Dockerfile loads this via
`gunicorn -c gunicorn_conf.py`.

Why each knob matters:
- `max_requests` + `max_requests_jitter` (M6) — recycles each worker after ~1000
  requests with jitter so they don't all restart at once. Mitigates slow memory
  growth from httpx connection pools and OpenTelemetry buffers under streaming
  workloads.
- `child_exit` hook (M6) — calls prometheus_client's mark_process_dead so the
  per-pid Gauge shard files in /tmp/prom_multiproc become eligible for
  collection. Without this, files accumulate as workers die/restart and the
  /metrics endpoint scrapes ever-growing stale state.
- `preload_app` (M7) — gunicorn imports the FastAPI app once before fork so
  module-level imports (FastAPI app instantiation, OpenAPI schema generation,
  prompt tables) are shared via copy-on-write across workers. **Lifespan still
  runs per-worker** (uvicorn invokes it once per process), so init_redis /
  init_qdrant / init_openai / initialize_exemplar_embeddings are NOT
  deduplicated; preload only saves Python-level RAM, not network round-trips.
"""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8001')}"
workers = int(os.environ.get("GUNICORN_WORKERS", "4"))
worker_class = "uvicorn.workers.UvicornWorker"

# Aligned with app/lifecycle.py SHUTDOWN_TIMEOUT — must stay in sync so SSE
# streams have time to drain before SIGKILL.
timeout = 120
graceful_timeout = 30

# Logs to stdout/stderr so the container runtime captures them.
accesslog = "-"
errorlog = "-"

# M6: worker recycling.
max_requests = 1000
max_requests_jitter = 100

# M7: preload — see module docstring for what this actually saves.
preload_app = True


def child_exit(server, worker):  # noqa: ARG001 — gunicorn hook signature
    """Mark the prometheus_client multiprocess shard for the dying worker as dead.

    Without this hook, /tmp/prom_multiproc keeps a file per (worker_pid, metric)
    forever — old workers' files leak into every subsequent scrape until the
    container restarts. mark_process_dead removes the per-pid Gauge files so
    only living workers contribute to the next aggregation.
    """
    from prometheus_client import multiprocess

    multiprocess.mark_process_dead(worker.pid)
