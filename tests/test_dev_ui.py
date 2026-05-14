"""Tests for the dev-only chat console at GET /dev-ui.

Dev-route gating itself (`if settings.enable_dev_routes: include_router(...)`)
is exercised statically the same way as test_rag_stats_endpoint.py — toggling
it at runtime would require reloading the whole app, which is brittle across
test ordering. conftest.py sets ENABLE_DEV_ROUTES=true so the route is mounted.
"""

from fastapi.testclient import TestClient


class TestDevUi:
    def test_dev_ui_serves_html(self):
        from app.main import app

        client = TestClient(app)
        resp = client.get("/dev-ui")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        body = resp.text.lower()
        # Sanity check: this really is the dev console, not someone else's HTML.
        assert "<!doctype html>" in body
        assert "health-ai dev console" in body
        # The HTML must speak to the streaming endpoint — if this assertion ever
        # fails we shipped a broken UI to ops.
        assert "/v1/chat/stream" in body

    def test_dev_ui_disables_caching(self):
        from app.main import app

        client = TestClient(app)
        resp = client.get("/dev-ui")

        # Edits to app/dev_ui/index.html should be visible after refresh in dev.
        # If browsers cache, the volume-mount story falls apart.
        assert resp.headers.get("cache-control") == "no-store"
