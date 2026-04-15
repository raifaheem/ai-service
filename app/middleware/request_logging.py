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
            metrics.record_request(500, duration_ms)
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

        metrics.record_request(status_code, duration_ms)

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
