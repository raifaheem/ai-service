import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings

logger = logging.getLogger(__name__)

from .routers.chat import router as chat_router
from .routers.conversations import router as conv_router
from .routers.articles import router as articles_router
from .routers.rag import router as rag_router
from .services.redis_client import init_redis, close_redis, get_redis
from .services.vector_client import init_qdrant, close_qdrant, get_qdrant
from .services.vector_store import ensure_qdrant_collection


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_redis()
    await init_qdrant()
    await ensure_qdrant_collection()
    try:
        yield
    finally:
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

    return {
        "status": status,
        "env": settings.app_env,
        "version": settings.app_version,
        "checks": checks,
    }


app.include_router(chat_router)
app.include_router(conv_router)
app.include_router(articles_router)

if settings.enable_dev_routes:
    app.include_router(rag_router)