import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .lifecycle import SHUTDOWN_TIMEOUT, active_streams, signal_shutdown
from .logging_config import setup_logging
from .security import auth_guard

setup_logging(settings.log_level, settings.log_format)

logger = logging.getLogger(__name__)

from .routers.articles import router as articles_router
from .routers.chat import router as chat_router
from .routers.conversations import router as conv_router
from .routers.rag import router as rag_router
from .services.redis_client import close_redis, get_redis, init_redis
from .services.vector_client import close_qdrant, get_qdrant, init_qdrant
from .services.vector_store import ensure_qdrant_collection


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_redis()
    await init_qdrant()
    await ensure_qdrant_collection()
    try:
        yield
    finally:
        signal_shutdown()
        streams = active_streams()
        if streams:
            logger.info("Waiting for %d active streams to finish (timeout=%ds)", len(streams), SHUTDOWN_TIMEOUT)
            _, pending = await asyncio.wait(streams, timeout=SHUTDOWN_TIMEOUT)
            if pending:
                logger.warning("Force-cancelling %d streams after shutdown timeout", len(pending))
                for task in pending:
                    task.cancel()
        await close_qdrant()
        await close_redis()


_API_DESCRIPTION = """\
Cognitive health AI assistant with RAG, conversation memory, intent classification,
and content safety. Called service-to-service from Laravel (`X-Service-Token`) and
directly from clients (`Authorization: Bearer <JWT>`, RS256).

**Supported locales:** `ru`, `en`, `kk`.

**Auth modes:**
- `X-Service-Token` + `X-User-Id` — server-to-server (Laravel).
- `Authorization: Bearer <jwt>` — direct client access (RS256-signed, `sub` claim required).

See [API_CONTRACT.md](https://github.com/) in the repository root for curl and
PHP integration examples, SSE event shapes, and error-code reference.
"""

_OPENAPI_TAGS = [
    {"name": "chat", "description": "Medical consultation endpoints (sync JSON + SSE streaming)."},
    {"name": "conversations", "description": "Retrieve and delete conversation history (owner-scoped)."},
    {"name": "articles", "description": "Ingest medical articles into the RAG corpus."},
    {"name": "dev-rag", "description": "Dev-only RAG inspection (requires `ENABLE_DEV_ROUTES=true`)."},
    {"name": "system", "description": "Liveness, metrics, service root."},
]

app = FastAPI(
    title=settings.app_name,
    description=_API_DESCRIPTION,
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs" if settings.app_env != "production" else None,
    redoc_url="/redoc" if settings.app_env != "production" else None,
    openapi_tags=_OPENAPI_TAGS,
    contact={"name": "Health AI Service", "url": "https://github.com/"},
)

if settings.allowed_origins == "*":
    origins = ["*"]
    allow_credentials = False
    if settings.app_env == "production":
        logger.warning(
            "CORS ALLOWED_ORIGINS is set to '*' in production. "
            "This is insecure — set specific origins for your domain."
        )
else:
    origins = [o.strip() for o in settings.allowed_origins.split(",")]
    allow_credentials = True

if settings.app_env == "production":
    allow_methods = ["GET", "POST", "DELETE", "OPTIONS"]
    allow_headers = ["Authorization", "Content-Type", "X-Service-Token", "X-User-Id", "X-Request-Id"]
else:
    allow_methods = ["*"]
    allow_headers = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=allow_credentials,
    allow_methods=allow_methods,
    allow_headers=allow_headers,
    expose_headers=["X-Request-Id", "X-API-Version", "X-Service-Version"],
)

from .middleware.api_version import APIVersionMiddleware
from .middleware.body_size import BodySizeLimitMiddleware
from .middleware.request_logging import RequestLoggingMiddleware

# 12 MB cap covers the articles endpoint's 10 MB uploads with headroom.
_MAX_BODY_BYTES = 12 * 1024 * 1024

app.add_middleware(BodySizeLimitMiddleware, max_bytes=_MAX_BODY_BYTES)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(APIVersionMiddleware)


@app.get(
    "/",
    tags=["system"],
    summary="Service root",
    description="Returns service identity and environment. Intended for smoke checks, not liveness.",
)
async def root():
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "env": settings.app_env,
        "status": "ok",
    }


@app.get(
    "/health",
    tags=["system"],
    summary="Liveness + dependency health",
    description=(
        "Returns `status: ok` when Redis, Qdrant, and the OpenAI circuit breaker are all healthy, "
        "otherwise `status: degraded` with per-dependency details in `checks`. "
        "Wire this to the orchestrator's liveness/readiness probe."
    ),
    responses={
        200: {
            "description": "Health snapshot",
            "content": {
                "application/json": {
                    "example": {
                        "status": "ok",
                        "env": "dev",
                        "version": "1.0.0",
                        "checks": {
                            "redis": "ok",
                            "qdrant": "ok",
                            "openai_circuit": "closed",
                            "qdrant_circuit": "closed",
                        },
                    }
                }
            },
        }
    },
)
async def health():
    from .services.circuit_breaker import openai_breaker, qdrant_breaker

    checks = {}
    status = "ok"

    try:
        await asyncio.wait_for(get_redis().ping(), timeout=2.0)
        checks["redis"] = "ok"
    except TimeoutError:
        checks["redis"] = "timeout"
        status = "degraded"
    except Exception:
        checks["redis"] = "unavailable"
        status = "degraded"

    try:
        await asyncio.wait_for(get_qdrant().get_collections(), timeout=2.0)
        checks["qdrant"] = "ok"
    except TimeoutError:
        checks["qdrant"] = "timeout"
        status = "degraded"
    except Exception:
        checks["qdrant"] = "unavailable"
        status = "degraded"

    openai_state = await openai_breaker.state
    qdrant_state = await qdrant_breaker.state
    checks["openai_circuit"] = openai_state
    checks["qdrant_circuit"] = qdrant_state
    if openai_state == "open":
        status = "degraded"

    return {
        "status": status,
        "env": settings.app_env,
        "version": settings.app_version,
        "checks": checks,
    }


@app.get(
    "/metrics",
    tags=["system"],
    summary="In-process metrics snapshot",
    description=(
        "Authenticated metrics endpoint (`X-Service-Token` or JWT): request counts, intent distribution, "
        "OpenAI token usage, RAG hit rate, 1h error rate, circuit-breaker states, plus live Redis "
        "(active conversations) and Qdrant (collection size) counters."
    ),
    dependencies=[Depends(auth_guard)],
)
async def get_metrics():
    from .metrics import metrics as app_metrics
    from .services.circuit_breaker import openai_breaker, qdrant_breaker

    snapshot = app_metrics.snapshot()
    snapshot["openai_circuit_state"] = await openai_breaker.state
    snapshot["qdrant_circuit_state"] = await qdrant_breaker.state

    try:
        r = get_redis()
        keys = await r.keys(f"{settings.redis_prefix}:conv:*:turns")
        snapshot["active_conversations"] = len(keys)
    except Exception:
        snapshot["active_conversations"] = -1

    try:
        info = await get_qdrant().get_collection(settings.qdrant_collection)
        snapshot["qdrant_collection_size"] = info.points_count
    except Exception:
        snapshot["qdrant_collection_size"] = -1

    return snapshot


app.include_router(chat_router)
app.include_router(conv_router)
app.include_router(articles_router)

if settings.enable_dev_routes:
    app.include_router(rag_router)
