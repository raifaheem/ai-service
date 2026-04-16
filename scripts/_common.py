"""Shared helpers for the knowledge-base seed + verify scripts.

Scripts are one-shot CLIs (not imported by the app), so a sync `httpx.Client`
is fine — it keeps retry, progress, and error handling flat and readable.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("knowledge_base")


def configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def _read_token_from_dotenv(env_path: Path) -> str | None:
    if not env_path.exists():
        return None
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("SERVICE_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def load_service_token(dotenv_path: Path | None = None) -> str:
    """Resolve the service token from the environment or `.env`.

    Mirrors what `pydantic-settings` does at app startup. Picks the first
    token when `SERVICE_TOKEN` is comma-separated (rotation support).
    """
    token = os.environ.get("SERVICE_TOKEN")
    if not token:
        if dotenv_path is None:
            dotenv_path = Path(__file__).resolve().parent.parent / ".env"
        token = _read_token_from_dotenv(dotenv_path)

    if not token:
        raise SystemExit(
            "SERVICE_TOKEN not found. Set it in the environment or in .env "
            "(the scripts use the same token the app does)."
        )

    return token.split(",")[0].strip()


def retry_with_backoff(
    fn: Callable[[], Any],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    label: str = "request",
) -> Any:
    """Run `fn` up to `attempts` times with exponential backoff.

    Retries on transport errors (timeouts, connection resets) and 5xx responses.
    4xx responses are re-raised immediately — those are caller errors and won't
    resolve by retrying.
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            status = exc.response.status_code
            if status < 500:
                raise
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "%s got HTTP %s (attempt %d/%d); retrying in %.1fs",
                label,
                status,
                attempt,
                attempts,
                delay,
            )
            time.sleep(delay)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "%s transport error %s (attempt %d/%d); retrying in %.1fs",
                label,
                exc.__class__.__name__,
                attempt,
                attempts,
                delay,
            )
            time.sleep(delay)

    assert last_exc is not None
    raise last_exc


class Progress:
    """Tiny stdout-only progress printer. No tqdm dependency."""

    def __init__(self, total: int, *, stream=sys.stdout):
        self.total = total
        self.stream = stream
        self.n = 0
        self.is_tty = stream.isatty()

    def update(self, label: str = "") -> None:
        self.n += 1
        line = f"[{self.n}/{self.total}] {label}"
        if self.is_tty:
            self.stream.write("\r" + line.ljust(80))
        else:
            self.stream.write(line + "\n")
        self.stream.flush()

    def done(self) -> None:
        if self.is_tty:
            self.stream.write("\n")
            self.stream.flush()


class ServiceClient:
    """Thin HTTP wrapper around the FastAPI service for the seed + verify scripts."""

    def __init__(self, base_url: str, service_token: str, *, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"X-Service-Token": service_token},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ServiceClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def analyze_article(
        self,
        *,
        title: str,
        text: str,
        language: str,
        source_id: str,
        index_chunks: bool = True,
    ) -> dict:
        """POST /v1/articles/analyze — chunk + analyze + index in one call.

        Note: the endpoint doesn't accept a `metadata` field directly. Topic +
        attribution live in the manifest (human-readable); if we later want them
        in Qdrant payloads we'll thread them through the router's
        `_run_article_pipeline`. For now the `source_id` is the join key back to
        the manifest.
        """
        resp = self._client.post(
            "/v1/articles/analyze",
            json={
                "title": title,
                "text": text,
                "language": language,
                "source_id": source_id,
                "index_chunks": index_chunks,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def delete_source(self, source_id: str) -> dict:
        """DELETE /v1/rag/source/{source_id} — idempotent overwrite helper."""
        resp = self._client.delete(f"/v1/rag/source/{source_id}")
        resp.raise_for_status()
        return resp.json()

    def rag_stats(self) -> dict:
        resp = self._client.get("/v1/rag/stats")
        resp.raise_for_status()
        return resp.json()

    def rag_search(self, *, query: str, language: str | None, limit: int = 5) -> dict:
        body: dict = {"query": query, "limit": limit}
        if language:
            body["language"] = language
        resp = self._client.post("/v1/rag/search", json=body)
        resp.raise_for_status()
        return resp.json()

    def metrics(self) -> dict:
        # /metrics is unauthenticated, but passing the header doesn't hurt.
        resp = self._client.get("/metrics")
        resp.raise_for_status()
        return resp.json()
