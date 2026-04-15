import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .logging_config import setup_logging

setup_logging(settings.log_level, settings.log_format)

logger = logging.getLogger(__name__)

from .routers.chat import router as chat_router
from .routers.conversations import router as conv_router
from .routers.articles import router as articles_router
from .routers.rag import router as rag_router
from .services.redis_client import init_redis, close_redis, get_redis
from .services.vector_client import init_qdrant, close_qdrant, get_qdrant
from .services.vector_store import ensure_qdrant_collection

# Track active streaming connections for graceful shutdown
_active_streams: set[asyncio.Task] = set()
_shutdown_event = asyncio.Event()
_SHUTDOWN_TIMEOUT = 30


def register_stream(task: asyncio.Task) -> None:
    _active_streams.add(task)
    task.add_done_callback(_active_streams.discard)


def is_shutting_down() -> bool:
    return _shutdown_event.is_set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_redis()
    await init_qdrant()
    await ensure_qdrant_collection()
    try:
        yield
    finally:
        _shutdown_event.set()
        if _active_streams:
            logger.info("Waiting for %d active streams to finish (timeout=%ds)", len(_active_streams), _SHUTDOWN_TIMEOUT)
            _, pending = await asyncio.wait(_active_streams, timeout=_SHUTDOWN_TIMEOUT)
            if pending:
                logger.warning("Force-cancelling %d streams after shutdown timeout", len(pending))
                for task in pending:
                    task.cancel()
        await close_qdrant()
        await close_redis()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

from .middleware.request_logging import RequestLoggingMiddleware

app.add_middleware(RequestLoggingMiddleware)


@app.get("/")
async def root():
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "env": settings.app_env,
        "status": "ok",
    }


@app.get("/health")
async def health():
    from .services.circuit_breaker import openai_breaker, qdrant_breaker

    checks = {}
    status = "ok"

    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"
        status = "degraded"

    try:
        await get_qdrant().get_collections()
        checks["qdrant"] = "ok"
    except Exception:
        checks["qdrant"] = "unavailable"
        status = "degraded"

    checks["openai_circuit"] = openai_breaker.state
    checks["qdrant_circuit"] = qdrant_breaker.state
    if openai_breaker.state == "open":
        status = "degraded"

    return {
        "status": status,
        "env": settings.app_env,
        "version": settings.app_version,
        "checks": checks,
    }


@app.get("/metrics")
async def get_metrics():
    from .metrics import metrics as app_metrics

    snapshot = app_metrics.snapshot()

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