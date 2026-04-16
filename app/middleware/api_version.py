from starlette.types import ASGIApp, Receive, Scope, Send

from ..config import settings

API_VERSION = "v1"


class APIVersionMiddleware:
    """Adds `X-API-Version` and `X-Service-Version` headers to every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._api_version = API_VERSION.encode("latin-1")
        self._service_version = settings.app_version.encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-api-version", self._api_version))
                headers.append((b"x-service-version", self._service_version))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
