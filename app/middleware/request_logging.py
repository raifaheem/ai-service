import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..context import set_request_id
from ..metrics import metrics

logger = logging.getLogger(__name__)

# Paths where request logging is at DEBUG level to avoid noise
_QUIET_PATHS = {"/health", "/metrics", "/"}

# Known route templates — keep path_tag cardinality bounded on /metrics so
# per-conversation ids don't blow up the Prometheus label space.
_STATIC_PATHS = {
    "/health",
    "/metrics",
    "/v1/chat",
    "/v1/chat/stream",
    "/v1/conversations",
    "/v1/articles/analyze",
    "/v1/articles/analyze-file",
    "/v1/rag/seed",
    "/v1/rag/stats",
    "/docs",
    "/redoc",
    "/openapi.json",
}


def _path_tag(path: str) -> str:
    """Normalize the request path to a fixed-cardinality tag for metrics.

    Keeps static routes as-is; collapses variable segments (e.g. a trailing
    conversation_id) to prevent label explosion.
    """
    if path in _STATIC_PATHS:
        return path
    # /v1/conversations/{id} → /v1/conversations/:id
    if path.startswith("/v1/conversations/"):
        return "/v1/conversations/:id"
    return "other"


# SECURITY: this middleware intentionally logs only method/path/status/duration
# metadata. It never logs request or response bodies, and never logs the
# Authorization, X-Service-Token, or Cookie headers — those would leak JWTs
# and service tokens into log aggregation systems.


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        set_request_id(request_id)

        method = request.method
        path = request.url.path
        client_ip = request.client.host if request.client else "-"
        request_content_length = request.headers.get("content-length", "-")

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            metrics.record_request(500, duration_ms, path_tag=_path_tag(path))
            logger.exception(
                "Unhandled exception",
                extra={
                    "method": method,
                    "path": path,
                    "status_code": 500,
                    "duration_ms": duration_ms,
                    "client_ip": client_ip,
                },
            )
            raise

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        status_code = response.status_code
        response_content_length = response.headers.get("content-length", "-")

        response.headers["X-Request-Id"] = request_id

        metrics.record_request(status_code, duration_ms, path_tag=_path_tag(path))

        log_level = logging.DEBUG if path in _QUIET_PATHS else logging.INFO
        logger.log(
            log_level,
            "%s %s %d %.1fms",
            method,
            path,
            status_code,
            duration_ms,
            extra={
                "method": method,
                "path": path,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "client_ip": client_ip,
                "request_content_length": request_content_length,
                "response_content_length": response_content_length,
            },
        )

        return response
