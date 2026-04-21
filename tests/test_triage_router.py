"""HTTP-layer tests for D.3.a triage router.

Uses the `auth_client` + `mock_redis` fixtures. LLM calls (`normalize_answer`,
`build_report`) are patched — router tests verify orchestration (auth,
ownership, state transitions, audit), not LLM behaviour.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.triage import (
    TRIAGE_FORM,
    NormalizedAnswer,
)


@pytest.fixture(autouse=True)
def _disable_rate_limit():
    """Rate limiting is exercised in test_rate_limit.py — for router behaviour
    tests it just burns through the 25-req budget when multiple tests run
    against the same mock_redis within a suite. Stub it per the chat-router
    pattern (see tests/test_chat_router.py:197)."""
    with patch("app.routers.triage.enforce_rate_limit", new_callable=AsyncMock):
        yield


def _ok(value=None, unparsed=False, red_flag=False, red_flag_reason=None, clarification=None):
    return NormalizedAnswer(
        value=value,
        unparsed=unparsed,
        red_flag=red_flag,
        red_flag_reason=red_flag_reason,
        clarification_needed=clarification,
    )


# --------------- POST session: start ---------------


class TestStartSession:
    async def test_post_without_session_id_creates_session_and_returns_first_question(
        self, auth_client
    ):
        resp = await auth_client.post("/v1/triage/session", json={"locale": "ru"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == "in_progress"
        assert body["step_index"] == 0
        assert body["total_steps"] == len(TRIAGE_FORM)
        assert body["session_id"]
        assert body["next_step"]["step_id"] == "primary_complaint"
        assert "disclaimer" in body
        # Session intro is prefixed to the first question only.
        assert len(body["next_step"]["question"]) > len(TRIAGE_FORM[0].prompts["ru"])

    async def test_post_missing_auth_returns_401(self, patched_app):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=patched_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/triage/session", json={"locale": "ru"})
        assert resp.status_code == 401

    async def test_choice_step_includes_choices_when_reached(self, auth_client):
        """The third step (`trajectory`) is a choice step — make sure the
        router exposes the allowed choices so the client can render a picker."""
        with patch(
            "app.routers.triage.normalize_answer",
            new_callable=AsyncMock,
            side_effect=[_ok(value="headache 3 days"), _ok(value="since monday")],
        ):
            r1 = await auth_client.post("/v1/triage/session", json={"locale": "en"})
            sid = r1.json()["session_id"]
            await auth_client.post(
                "/v1/triage/session",
                json={"session_id": sid, "answer": "head pain", "locale": "en"},
            )
            r3 = await auth_client.post(
                "/v1/triage/session",
                json={"session_id": sid, "answer": "monday", "locale": "en"},
            )
        body = r3.json()
        assert body["next_step"]["step_id"] == "trajectory"
        assert body["next_step"]["choices"] == ["worsening", "stable", "improving"]


# --------------- POST session: advance path ---------------


class TestAdvanceSession:
    async def test_advance_without_answer_is_400(self, auth_client):
        start = await auth_client.post("/v1/triage/session", json={"locale": "ru"})
        sid = start.json()["session_id"]
        resp = await auth_client.post(
            "/v1/triage/session",
            json={"session_id": sid, "locale": "ru"},
        )
        assert resp.status_code == 400
        assert "answer" in resp.json()["detail"].lower()

    async def test_nonexistent_session_is_404(self, auth_client):
        resp = await auth_client.post(
            "/v1/triage/session",
            json={
                "session_id": "00000000-0000-4000-8000-000000000000",
                "answer": "x",
                "locale": "ru",
            },
        )
        assert resp.status_code == 404

    async def test_third_party_session_is_403(self, auth_client, patched_app):
        """Start a session as test-user-123, then try to advance it as a different user."""
        start = await auth_client.post("/v1/triage/session", json={"locale": "ru"})
        sid = start.json()["session_id"]

        from httpx import ASGITransport, AsyncClient

        other_headers = {"X-Service-Token": "test-token", "X-User-Id": "someone-else"}
        transport = ASGITransport(app=patched_app)
        async with AsyncClient(transport=transport, base_url="http://test", headers=other_headers) as c:
            resp = await c.post(
                "/v1/triage/session",
                json={"session_id": sid, "answer": "hi", "locale": "ru"},
            )
        assert resp.status_code == 403

    async def test_valid_answer_advances_step(self, auth_client):
        with patch(
            "app.routers.triage.normalize_answer",
            new_callable=AsyncMock,
            return_value=_ok(value="terrible migraine for 2 days"),
        ):
            start = await auth_client.post("/v1/triage/session", json={"locale": "en"})
            sid = start.json()["session_id"]
            resp = await auth_client.post(
                "/v1/triage/session",
                json={"session_id": sid, "answer": "migraine for 2 days", "locale": "en"},
            )
        body = resp.json()
        assert body["state"] == "in_progress"
        assert body["step_index"] == 1
        assert body["next_step"]["step_id"] == "onset"


# --------------- POST session: red flag ---------------


class TestRedFlagExit:
    async def test_red_flag_on_primary_complaint_exits_with_emergency_phone(
        self, auth_client
    ):
        with patch(
            "app.routers.triage.normalize_answer",
            new_callable=AsyncMock,
            return_value=_ok(
                value="severe chest pain radiating to left arm",
                red_flag=True,
                red_flag_reason="Severe chest pain with radiation (possible ACS)",
            ),
        ):
            start = await auth_client.post(
                "/v1/triage/session",
                json={"locale": "en", "region": "KZ"},
            )
            sid = start.json()["session_id"]
            resp = await auth_client.post(
                "/v1/triage/session",
                json={"session_id": sid, "answer": "severe chest pain", "locale": "en", "region": "KZ"},
            )
        body = resp.json()
        assert body["state"] == "red_flag_exit"
        assert "chest pain" in body["detected_red_flag"].lower()
        # D.1 integration: KZ region → "112 / 103", NOT "911" — the whole point
        # of regionalizing the emergency number.
        assert body["emergency_phone"] == "112 / 103"
        assert "112 / 103" in body["emergency_message"]
        assert "911" not in body["emergency_message"]

    async def test_us_region_resolves_to_911(self, auth_client):
        with patch(
            "app.routers.triage.normalize_answer",
            new_callable=AsyncMock,
            return_value=_ok(
                value="severe chest pain",
                red_flag=True,
                red_flag_reason="Severe chest pain",
            ),
        ):
            start = await auth_client.post(
                "/v1/triage/session",
                json={"locale": "en", "region": "US"},
            )
            sid = start.json()["session_id"]
            resp = await auth_client.post(
                "/v1/triage/session",
                json={"session_id": sid, "answer": "chest pain", "locale": "en", "region": "US"},
            )
        assert resp.json()["emergency_phone"] == "911"

    async def test_advance_after_red_flag_is_409(self, auth_client):
        """Once terminated, POST must refuse to continue the same session."""
        with patch(
            "app.routers.triage.normalize_answer",
            new_callable=AsyncMock,
            return_value=_ok(
                value="severe chest pain",
                red_flag=True,
                red_flag_reason="chest pain",
            ),
        ):
            start = await auth_client.post("/v1/triage/session", json={"locale": "en"})
            sid = start.json()["session_id"]
            await auth_client.post(
                "/v1/triage/session",
                json={"session_id": sid, "answer": "chest pain", "locale": "en"},
            )
            # Second advance — session is now terminal.
            resp = await auth_client.post(
                "/v1/triage/session",
                json={"session_id": sid, "answer": "anything", "locale": "en"},
            )
        assert resp.status_code == 409
        assert "red_flag_exit" in resp.json()["detail"].lower()


# --------------- POST session: completion ---------------


class TestCompletion:
    async def test_full_session_ends_with_report(self, auth_client):
        from app.schemas_triage import (
            SpecialistRecommendation,
            StructuredAnswers,
            TriageReport,
        )

        stub_report = TriageReport(
            clinical_summary="Patient reports 3-day headache, severity 6.",
            structured=StructuredAnswers(primary_complaint="headache", severity=6),
            specialist_recommendation=SpecialistRecommendation(
                category="neurologist",
                rationale="Recurrent headache pattern warrants neurological review.",
            ),
            detected_red_flags=[],
        )

        # 10 identical normalizer responses — one per step.
        def canned_value(_step, _answer, _locale):
            return _ok(value="x")

        with (
            patch(
                "app.routers.triage.normalize_answer",
                new_callable=AsyncMock,
                side_effect=[_ok(value=v) for v in ["x"] * len(TRIAGE_FORM)],
            ),
            patch(
                "app.routers.triage.build_report",
                new_callable=AsyncMock,
                return_value=stub_report,
            ),
        ):
            start = await auth_client.post("/v1/triage/session", json={"locale": "en"})
            sid = start.json()["session_id"]
            last_resp = None
            for _ in range(len(TRIAGE_FORM)):
                last_resp = await auth_client.post(
                    "/v1/triage/session",
                    json={"session_id": sid, "answer": "x", "locale": "en"},
                )
        assert last_resp is not None
        body = last_resp.json()
        assert body["state"] == "completed"
        assert body["report"]["specialist_recommendation"]["category"] == "neurologist"
        assert body["report"]["clinical_summary"].startswith("Patient reports")


# --------------- GET /session/{id} ---------------


class TestGetSession:
    async def test_snapshot_returned(self, auth_client):
        start = await auth_client.post("/v1/triage/session", json={"locale": "ru", "region": "KZ"})
        sid = start.json()["session_id"]
        resp = await auth_client.get(f"/v1/triage/session/{sid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == sid
        assert body["state"] == "in_progress"
        assert body["locale"] == "ru"
        assert body["region"] == "KZ"
        assert body["step_index"] == 0
        assert "answers" not in body  # recovery view deliberately omits answers

    async def test_snapshot_missing_is_404(self, auth_client):
        resp = await auth_client.get("/v1/triage/session/00000000-0000-4000-8000-000000000000")
        assert resp.status_code == 404

    async def test_snapshot_forbidden_for_other_user(self, auth_client, patched_app):
        start = await auth_client.post("/v1/triage/session", json={"locale": "ru"})
        sid = start.json()["session_id"]

        from httpx import ASGITransport, AsyncClient

        other = {"X-Service-Token": "test-token", "X-User-Id": "other-user"}
        transport = ASGITransport(app=patched_app)
        async with AsyncClient(transport=transport, base_url="http://test", headers=other) as c:
            resp = await c.get(f"/v1/triage/session/{sid}")
        assert resp.status_code == 403


# --------------- DELETE /session/{id} ---------------


class TestDeleteSession:
    async def test_delete_clears_state(self, auth_client):
        start = await auth_client.post("/v1/triage/session", json={"locale": "ru"})
        sid = start.json()["session_id"]
        resp = await auth_client.delete(f"/v1/triage/session/{sid}")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True, "session_id": sid}

        # Second delete → 404, confirming keys are gone.
        resp2 = await auth_client.delete(f"/v1/triage/session/{sid}")
        assert resp2.status_code == 404

    async def test_delete_forbidden_for_other_user(self, auth_client, patched_app):
        start = await auth_client.post("/v1/triage/session", json={"locale": "ru"})
        sid = start.json()["session_id"]

        from httpx import ASGITransport, AsyncClient

        other = {"X-Service-Token": "test-token", "X-User-Id": "other-user"}
        transport = ASGITransport(app=patched_app)
        async with AsyncClient(transport=transport, base_url="http://test", headers=other) as c:
            resp = await c.delete(f"/v1/triage/session/{sid}")
        assert resp.status_code == 403


# --------------- Clarification loop via HTTP ---------------


class TestClarificationViaHttp:
    async def test_clarification_returns_same_step_with_clarification_text(self, auth_client):
        with patch(
            "app.routers.triage.normalize_answer",
            new_callable=AsyncMock,
            return_value=_ok(clarification="Please be more specific"),
        ):
            start = await auth_client.post("/v1/triage/session", json={"locale": "en"})
            sid = start.json()["session_id"]
            resp = await auth_client.post(
                "/v1/triage/session",
                json={"session_id": sid, "answer": "vague", "locale": "en"},
            )
        body = resp.json()
        assert body["step_index"] == 0  # stayed on the same step
        assert body["next_step"]["step_id"] == "primary_complaint"
        assert body["next_step"]["clarification"] == "Please be more specific"
