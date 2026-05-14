"""Dev-only single-page chat console for manual LLM behavior testing.

Mounted only when ENABLE_DEV_ROUTES=true (see app/main.py). The HTML talks to
/v1/chat/stream over SSE using the same X-Service-Token + X-User-Id headers as
any other client — auth is enforced at the chat endpoint, not here.

Read-on-each-request is intentional: with a `./app:/app/app:ro` bind mount,
editing the HTML and refreshing the browser is enough to iterate.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dev-ui"])

_INDEX_PATH = Path(__file__).resolve().parent.parent / "dev_ui" / "index.html"


@router.get("/dev-ui", response_class=HTMLResponse, include_in_schema=False)
async def dev_ui_index() -> HTMLResponse:
    return HTMLResponse(
        _INDEX_PATH.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )
