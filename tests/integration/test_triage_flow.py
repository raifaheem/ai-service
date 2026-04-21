"""End-to-end integration test for the D.3.a triage flow.

Drives the full 10-step sequence through the real FastAPI router with LLM
calls mocked at the seam — same pattern as test_chat_parity.py. Focus is on
the happy path and the three non-trivial branches (red-flag exit mid-flow,
recovery via GET, abandon via DELETE).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.schemas_triage import (
    SpecialistRecommendation,
    StructuredAnswers,
    TriageReport,
)
from app.services.triage import TRIAGE_FORM, NormalizedAnswer


@pytest.fixture(autouse=True)
def _disable_rate_limit():
    with patch("app.routers.triage.enforce_rate_limit", new_callable=AsyncMock):
        yield


def _typed_value_for_step(step_index: int):
    """Pick a plausible normalized value for the step at this index."""
    step = TRIAGE_FORM[step_index]
    if step.kind == "int_scale":
        return 4  # mild-moderate, below the severity>=8 heuristic red-flag threshold
    if step.kind == "boolean":
        return False
    if step.kind == "choice":
        return step.choices[0]  # type: ignore[index]
    return f"normalized-answer-{step.id}"


async def test_happy_path_ten_steps_to_report(auth_client):
    """Full 10-step traversal with a neurologist-routing report."""
    normalized_side_effects = [
        NormalizedAnswer(value=_typed_value_for_step(i)) for i in range(len(TRIAGE_FORM))
    ]
    stub_report = TriageReport(
        clinical_summary=(
            "Patient reports a 3-day episode of unilateral pulsating headache with severity 4/10. "
            "Worsening pattern, no red-flag symptoms on explicit screen. No known contraindications."
        ),
        structured=StructuredAnswers(
            primary_complaint="unilateral pulsating headache",
            severity=4,
            trajectory="worsening",
        ),
        specialist_recommendation=SpecialistRecommendation(
            category="neurologist",
            rationale="Recurrent unilateral pulsating headache pattern warrants neurological review.",
        ),
        detected_red_flags=[],
    )

    with (
        patch(
            "app.routers.triage.normalize_answer",
            new_callable=AsyncMock,
            side_effect=normalized_side_effects,
        ),
        patch(
            "app.routers.triage.build_report",
            new_callable=AsyncMock,
            return_value=stub_report,
        ),
    ):
        # Start.
        start = await auth_client.post("/v1/triage/session", json={"locale": "en", "region": "US"})
        assert start.status_code == 200
        body = start.json()
        sid = body["session_id"]
        assert body["state"] == "in_progress"
        assert body["next_step"]["step_id"] == "primary_complaint"

        # Walk all 10 steps.
        final_body = None
        for step_idx in range(len(TRIAGE_FORM)):
            resp = await auth_client.post(
                "/v1/triage/session",
                json={"session_id": sid, "answer": "raw client answer", "locale": "en"},
            )
            assert resp.status_code == 200
            final_body = resp.json()
            if step_idx < len(TRIAGE_FORM) - 1:
                assert final_body["state"] == "in_progress"
                assert final_body["step_index"] == step_idx + 1

    assert final_body is not None
    assert final_body["state"] == "completed"
    assert final_body["report"]["specialist_recommendation"]["category"] == "neurologist"
    assert final_body["report"]["clinical_summary"].startswith("Patient reports")
    assert final_body["report"]["structured"]["primary_complaint"] == "unilateral pulsating headache"
    # Disclaimer carried through.
    assert "diagnosis" in final_body["disclaimer"].lower()


async def test_red_flag_exit_mid_flow_with_kz_region(auth_client):
    """Session starts, one benign step lands, then accompanying-symptoms step
    raises a red flag — exit with a KZ-localized emergency number.
    """
    normalized_sequence = [
        NormalizedAnswer(value="intermittent headache"),  # primary_complaint
        NormalizedAnswer(value="2 days ago"),  # onset
        NormalizedAnswer(value="worsening"),  # trajectory
        NormalizedAnswer(value=5),  # severity
        # accompanying — step.red_flag_check=True; red flag fires here.
        NormalizedAnswer(
            value="severe chest pain, shortness of breath",
            red_flag=True,
            red_flag_reason="Severe chest pain with shortness of breath",
        ),
    ]

    with patch(
        "app.routers.triage.normalize_answer",
        new_callable=AsyncMock,
        side_effect=normalized_sequence,
    ):
        start = await auth_client.post("/v1/triage/session", json={"locale": "ru", "region": "KZ"})
        sid = start.json()["session_id"]

        final_body = None
        for _ in range(5):
            resp = await auth_client.post(
                "/v1/triage/session",
                json={"session_id": sid, "answer": "raw", "locale": "ru", "region": "KZ"},
            )
            final_body = resp.json()
            if final_body["state"] != "in_progress":
                break

    assert final_body is not None
    assert final_body["state"] == "red_flag_exit"
    # D.1 regionalization: KZ → "112 / 103", never "911".
    assert final_body["emergency_phone"] == "112 / 103"
    assert "112 / 103" in final_body["emergency_message"]
    assert "911" not in final_body["emergency_message"]
    assert "Severe chest pain" in final_body["detected_red_flag"]


async def test_recovery_via_get_in_the_middle(auth_client):
    """Partway through a session, GET returns a usable recovery snapshot and
    doesn't leak the collected answers to the client."""
    with patch(
        "app.routers.triage.normalize_answer",
        new_callable=AsyncMock,
        side_effect=[
            NormalizedAnswer(value="tension headache"),
            NormalizedAnswer(value="last Monday"),
        ],
    ):
        start = await auth_client.post("/v1/triage/session", json={"locale": "en"})
        sid = start.json()["session_id"]
        await auth_client.post(
            "/v1/triage/session",
            json={"session_id": sid, "answer": "my head", "locale": "en"},
        )
        await auth_client.post(
            "/v1/triage/session",
            json={"session_id": sid, "answer": "Monday", "locale": "en"},
        )

        snap = await auth_client.get(f"/v1/triage/session/{sid}")
    assert snap.status_code == 200
    body = snap.json()
    assert body["state"] == "in_progress"
    assert body["step_index"] == 2
    assert body["total_steps"] == len(TRIAGE_FORM)
    # Recovery snapshot must NOT expose collected answers.
    assert "answers" not in body


async def test_abandoned_session_cannot_be_resumed(auth_client):
    """DELETE removes state; a subsequent POST with the same id is 404."""
    with patch(
        "app.routers.triage.normalize_answer",
        new_callable=AsyncMock,
        return_value=NormalizedAnswer(value="x"),
    ):
        start = await auth_client.post("/v1/triage/session", json={"locale": "en"})
        sid = start.json()["session_id"]

        deleted = await auth_client.delete(f"/v1/triage/session/{sid}")
        assert deleted.status_code == 200

        resume = await auth_client.post(
            "/v1/triage/session",
            json={"session_id": sid, "answer": "anything", "locale": "en"},
        )
        assert resume.status_code == 404
