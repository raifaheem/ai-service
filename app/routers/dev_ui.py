"""Dev-only single-page chat console for manual LLM behavior testing.

Mounted only when ENABLE_DEV_ROUTES=true (see app/main.py). The HTML talks to
/v1/chat/stream over SSE using the same X-Service-Token + X-User-Id headers as
any other client — auth is enforced at the chat endpoint, not here.

Read-on-each-request is intentional: with a `./app:/app/app:ro` bind mount,
editing the HTML and refreshing the browser is enough to iterate.

A streaming reverse-proxy at /dev-ui/prod-proxy/{path:path} forwards the same
request shape to the deployed Fly app so the dev-ui can be pointed at prod
without touching prod's ALLOWED_ORIGINS — the browser only ever sees a
same-origin request.
"""

from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse

router = APIRouter(tags=["dev-ui"])

_INDEX_PATH = Path(__file__).resolve().parent.parent / "dev_ui" / "index.html"

_PROD_BASE_URL = "https://health-ai-service.fly.dev"

# Hop-by-hop + per-connection headers that must not be forwarded (RFC 7230 §6.1
# plus Host which httpx rewrites itself).
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)


@router.get("/dev-ui", response_class=HTMLResponse, include_in_schema=False)
async def dev_ui_index() -> HTMLResponse:
    return HTMLResponse(
        _INDEX_PATH.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )


@router.api_route(
    "/dev-ui/prod-proxy/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    include_in_schema=False,
)
async def prod_proxy(path: str, request: Request) -> StreamingResponse:
    """Stream-forward a request to the deployed Fly app, preserving SSE."""
    target = f"{_PROD_BASE_URL}/{path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"

    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    body = await request.body()

    client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
    req = client.build_request(
        request.method,
        target,
        headers=fwd_headers,
        content=body if body else None,
    )
    upstream = await client.send(req, stream=True)

    async def body_iter():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    resp_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP and k.lower() != "content-encoding"
    }
    return StreamingResponse(
        body_iter(),
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )
