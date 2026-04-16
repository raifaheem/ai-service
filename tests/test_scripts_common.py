"""Tests for shared helpers in scripts/_common.py."""

import io
from unittest.mock import MagicMock, patch

import httpx
import pytest

from scripts._common import (
    Progress,
    ServiceClient,
    load_service_token,
    retry_with_backoff,
)


# --------------- load_service_token ---------------


class TestLoadServiceToken:
    def test_reads_from_env(self, monkeypatch):
        monkeypatch.setenv("SERVICE_TOKEN", "from-env")
        assert load_service_token() == "from-env"

    def test_picks_first_when_comma_separated(self, monkeypatch):
        monkeypatch.setenv("SERVICE_TOKEN", "primary,secondary,tertiary")
        assert load_service_token() == "primary"

    def test_falls_back_to_dotenv(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SERVICE_TOKEN", raising=False)
        fake_env = tmp_path / ".env"
        fake_env.write_text(
            '# comment\nOPENAI_API_KEY=x\nSERVICE_TOKEN="file-token"\n',
            encoding="utf-8",
        )
        assert load_service_token(dotenv_path=fake_env) == "file-token"

    def test_strips_quotes_in_dotenv_value(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SERVICE_TOKEN", raising=False)
        fake_env = tmp_path / ".env"
        fake_env.write_text("SERVICE_TOKEN='single-quoted'\n", encoding="utf-8")
        assert load_service_token(dotenv_path=fake_env) == "single-quoted"

    def test_exits_when_not_found(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SERVICE_TOKEN", raising=False)
        missing = tmp_path / "nonexistent.env"
        with pytest.raises(SystemExit, match="SERVICE_TOKEN not found"):
            load_service_token(dotenv_path=missing)

    def test_exits_when_dotenv_lacks_key(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SERVICE_TOKEN", raising=False)
        fake_env = tmp_path / ".env"
        fake_env.write_text("OPENAI_API_KEY=x\n", encoding="utf-8")
        with pytest.raises(SystemExit, match="SERVICE_TOKEN not found"):
            load_service_token(dotenv_path=fake_env)


# --------------- retry_with_backoff ---------------


class TestRetryWithBackoff:
    def test_succeeds_on_first_attempt(self):
        fn = MagicMock(return_value="ok")
        result = retry_with_backoff(fn, attempts=3, base_delay=0.0)
        assert result == "ok"
        assert fn.call_count == 1

    def test_retries_on_5xx_then_succeeds(self):
        resp = httpx.Response(503, request=httpx.Request("GET", "/"))
        fn = MagicMock(side_effect=[
            httpx.HTTPStatusError("503", request=resp.request, response=resp),
            "ok",
        ])
        with patch("scripts._common.time.sleep"):
            result = retry_with_backoff(fn, attempts=3, base_delay=0.0)
        assert result == "ok"
        assert fn.call_count == 2

    def test_reraises_4xx_immediately(self):
        resp = httpx.Response(400, request=httpx.Request("GET", "/"))
        err = httpx.HTTPStatusError("400", request=resp.request, response=resp)
        fn = MagicMock(side_effect=err)
        with patch("scripts._common.time.sleep"):
            with pytest.raises(httpx.HTTPStatusError):
                retry_with_backoff(fn, attempts=3, base_delay=0.0)
        assert fn.call_count == 1

    def test_gives_up_after_max_attempts(self):
        resp = httpx.Response(500, request=httpx.Request("GET", "/"))
        err = httpx.HTTPStatusError("500", request=resp.request, response=resp)
        fn = MagicMock(side_effect=err)
        with patch("scripts._common.time.sleep"):
            with pytest.raises(httpx.HTTPStatusError):
                retry_with_backoff(fn, attempts=3, base_delay=0.0)
        assert fn.call_count == 3

    def test_retries_transport_errors(self):
        fn = MagicMock(side_effect=[
            httpx.ConnectError("down", request=httpx.Request("GET", "/")),
            "ok",
        ])
        with patch("scripts._common.time.sleep"):
            result = retry_with_backoff(fn, attempts=3, base_delay=0.0)
        assert result == "ok"
        assert fn.call_count == 2


# --------------- Progress ---------------


class TestProgress:
    def test_prints_line_per_update_non_tty(self):
        buf = io.StringIO()
        p = Progress(total=3, stream=buf)
        p.is_tty = False  # force non-TTY output path
        p.update("first")
        p.update("second")
        out = buf.getvalue()
        assert "[1/3] first" in out
        assert "[2/3] second" in out

    def test_redraws_in_tty_mode(self):
        buf = io.StringIO()
        p = Progress(total=2, stream=buf)
        p.is_tty = True
        p.update("one")
        p.done()
        out = buf.getvalue()
        assert "\r" in out
        assert out.endswith("\n")

    def test_done_noop_when_non_tty(self):
        buf = io.StringIO()
        p = Progress(total=1, stream=buf)
        p.is_tty = False
        p.update("x")
        p.done()
        # Only the update line should be in the buffer; no extra newline from done()
        assert buf.getvalue().count("\n") == 1


# --------------- ServiceClient ---------------


class TestServiceClient:
    def test_sends_service_token_header(self):
        with patch("scripts._common.httpx.Client") as ctor:
            instance = MagicMock()
            ctor.return_value = instance
            ServiceClient("http://x", "tok")

        call_kwargs = ctor.call_args.kwargs
        assert call_kwargs["headers"] == {"X-Service-Token": "tok"}
        assert call_kwargs["base_url"] == "http://x"

    def test_strips_trailing_slash(self):
        with patch("scripts._common.httpx.Client"):
            c = ServiceClient("http://x/", "tok")
        assert c.base_url == "http://x"

    def test_analyze_article_posts_expected_payload(self):
        with patch("scripts._common.httpx.Client") as ctor:
            http = MagicMock()
            resp = MagicMock()
            resp.json.return_value = {"indexed_chunks": 2}
            resp.raise_for_status = MagicMock()
            http.post.return_value = resp
            ctor.return_value = http

            c = ServiceClient("http://x", "tok")
            result = c.analyze_article(
                title="T", text="body", language="ru", source_id="sid",
            )

        http.post.assert_called_once_with(
            "/v1/articles/analyze",
            json={
                "title": "T", "text": "body", "language": "ru",
                "source_id": "sid", "index_chunks": True,
            },
        )
        assert result["indexed_chunks"] == 2

    def test_delete_source_hits_right_path(self):
        with patch("scripts._common.httpx.Client") as ctor:
            http = MagicMock()
            resp = MagicMock()
            resp.json.return_value = {"source_id": "x", "deleted": 0}
            resp.raise_for_status = MagicMock()
            http.delete.return_value = resp
            ctor.return_value = http

            c = ServiceClient("http://x", "tok")
            c.delete_source("who-headache-2024")

        http.delete.assert_called_once_with("/v1/rag/source/who-headache-2024")

    def test_rag_search_includes_language_when_provided(self):
        with patch("scripts._common.httpx.Client") as ctor:
            http = MagicMock()
            resp = MagicMock()
            resp.json.return_value = {"results": []}
            resp.raise_for_status = MagicMock()
            http.post.return_value = resp
            ctor.return_value = http

            c = ServiceClient("http://x", "tok")
            c.rag_search(query="q", language="ru", limit=3)

        http.post.assert_called_once_with(
            "/v1/rag/search",
            json={"query": "q", "limit": 3, "language": "ru"},
        )

    def test_rag_search_omits_language_when_none(self):
        with patch("scripts._common.httpx.Client") as ctor:
            http = MagicMock()
            resp = MagicMock()
            resp.json.return_value = {"results": []}
            resp.raise_for_status = MagicMock()
            http.post.return_value = resp
            ctor.return_value = http

            c = ServiceClient("http://x", "tok")
            c.rag_search(query="q", language=None)

        body = http.post.call_args.kwargs["json"]
        assert "language" not in body

    def test_context_manager_closes(self):
        with patch("scripts._common.httpx.Client") as ctor:
            http = MagicMock()
            ctor.return_value = http
            with ServiceClient("http://x", "tok") as c:
                pass
        http.close.assert_called_once()
