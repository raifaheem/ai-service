"""Pre-consultation triage endpoints (D.3.a).

Three routes:
- POST /v1/triage/session — start or advance a session.
- GET  /v1/triage/session/{id} — recovery snapshot (no answers).
- DELETE /v1/triage/session/{id} — abandon.

The router is deliberately thin: it owns auth, rate limiting, owner check,
tracing spans, audit events, and turning the triage service's pure state
machine into the HTTP response shape. All triage logic lives in
app/services/triage.py; persistence in app/services/triage_memory.py.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from ..context import get_request_id, set_conversation_id, set_user_id
from ..prompts_triage import EMERGENCY_MESSAGE, SESSION_INTRO
from ..schemas_triage import (
    TriageAdvanceRequest,
    TriageAdvanceResponse,
    TriageSessionSnapshot,
)
from ..security import auth_guard, resolve_user_id
from ..services import triage_memory
from ..services.audit import (
    EVENT_TRIAGE_ABANDONED,
    EVENT_TRIAGE_COMPLETE,
    EVENT_TRIAGE_RED_FLAG_EXIT,
    EVENT_TRIAGE_START,
    EVENT_TRIAGE_STEP,
    record_audit_event,
)
from ..services.i18n import get_disclaimer, get_emergency_phone, normalize_locale
from ..services.rate_limit import enforce_rate_limit
from ..services.triage import (
    TRIAGE_FORM,
    TriageSession,
    TriageState,
    advance,
    build_report,
    normalize_answer,
)
from ..tracing import tracer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/triage", tags=["triage"])


_ADVANCE_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"description": "Missing `answer` for an existing session, or step_index out of range."},
    401: {"description": "Missing or invalid authentication."},
    403: {"description": "Session belongs to a different user."},
    404: {"description": "Session not found or expired."},
    409: {"description": "Session already terminated (red_flag_exit or completed) — start a new one."},
    429: {"description": "Rate limit exceeded."},
}


def _next_step_payload(session: TriageSession, clarification: str | None = None) -> dict[str, Any]:
    """Shape the `next_step` block returned to the client."""
    step = TRIAGE_FORM[session.current_step_index]
    payload: dict[str, Any] = {
        "step_id": step.id,
        "question": step.question(session.locale),
        "kind": step.kind,
    }
    if step.choices is not None:
        payload["choices"] = list(step.choices)
    if step.int_range is not None:
        payload["range"] = {"min": step.int_range[0], "max": step.int_range[1]}
    if clarification:
        payload["clarification"] = clarification
    return payload


def _session_intro(locale: str) -> str:
    loc = normalize_locale(locale)
    return SESSION_INTRO.get(loc, SESSION_INTRO["ru"])


@router.post(
    "/session",
    response_model=TriageAdvanceResponse,
    summary="Start or advance a triage session",
    description=(
        "Omit `session_id` to start a new triage — the server returns a freshly created "
        "`session_id` and the first question. Supply `session_id + answer` to advance "
        "one step. On a detected red flag the session terminates with state=red_flag_exit "
        "and an emergency_phone localized via the request's `region` (D.1 logic)."
    ),
    responses=_ADVANCE_RESPONSES,
)
async def advance_session(
    req: TriageAdvanceRequest,
    auth=Depends(auth_guard),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> TriageAdvanceResponse:
    user_id = resolve_user_id(auth, x_user_id)
    set_user_id(user_id)
    locale = normalize_locale(req.locale)
    disclaimer = get_disclaimer(locale)

    await enforce_rate_limit(f"user:{user_id}")

    # --- session creation branch ---
    if req.session_id is None:
        session = TriageSession.new(user_id=user_id, locale=locale, region=req.region)
        set_conversation_id(session.session_id)
        await triage_memory.save_session(session)
        await record_audit_event(
            EVENT_TRIAGE_START,
            user_id=user_id,
            conversation_id=session.session_id,
            request_id=get_request_id(),
            locale=locale,
            region=req.region or "",
        )
        first_step = TRIAGE_FORM[0]
        return TriageAdvanceResponse(
            session_id=session.session_id,
            state="in_progress",
            step_index=0,
            total_steps=len(TRIAGE_FORM),
            next_step={
                "step_id": first_step.id,
                "question": _session_intro(locale) + " " + first_step.question(locale),
                "kind": first_step.kind,
                **({"choices": list(first_step.choices)} if first_step.choices else {}),
                **(
                    {"range": {"min": first_step.int_range[0], "max": first_step.int_range[1]}}
                    if first_step.int_range
                    else {}
                ),
            },
            disclaimer=disclaimer,
        )

    # --- advance branch ---
    set_conversation_id(req.session_id)
    session = await triage_memory.load_session(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Triage session not found or expired")

    owner = await triage_memory.get_owner(req.session_id)
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Access denied to this triage session")

    if session.state is not TriageState.IN_PROGRESS:
        # 409 only for terminal-but-not-deleted states. A deleted session is 404 above.
        raise HTTPException(
            status_code=409,
            detail=f"Triage session is {session.state.value}; start a new session",
        )

    if not req.answer:
        raise HTTPException(status_code=400, detail="`answer` is required when `session_id` is supplied")

    current_step = TRIAGE_FORM[session.current_step_index]

    with tracer.start_as_current_span("triage.normalize") as span:
        span.set_attribute("triage.step_id", current_step.id)
        span.set_attribute("triage.step_index", session.current_step_index)
        normalized = await normalize_answer(current_step, req.answer, locale)
        span.set_attribute("triage.red_flag", bool(normalized.red_flag))
        span.set_attribute("triage.unparsed", bool(normalized.unparsed))

    result = advance(session, normalized)

    # --- red flag exit ---
    if result.kind == "red_flag_exit":
        await triage_memory.save_session(session)
        emergency_phone = get_emergency_phone(session.region, locale)
        message = EMERGENCY_MESSAGE.get(locale, EMERGENCY_MESSAGE["ru"]).format(emergency_phone=emergency_phone)
        await record_audit_event(
            EVENT_TRIAGE_RED_FLAG_EXIT,
            user_id=user_id,
            conversation_id=session.session_id,
            request_id=get_request_id(),
            step_id=current_step.id,
            step_index=session.current_step_index,
            red_flag_reason=result.red_flag_reason or "",
            locale=locale,
        )
        return TriageAdvanceResponse(
            session_id=session.session_id,
            state="red_flag_exit",
            step_index=session.current_step_index,
            total_steps=len(TRIAGE_FORM),
            emergency_message=message,
            detected_red_flag=result.red_flag_reason,
            emergency_phone=emergency_phone,
            disclaimer=disclaimer,
        )

    # --- clarification loop ---
    if result.kind == "clarify":
        await triage_memory.save_session(session)
        return TriageAdvanceResponse(
            session_id=session.session_id,
            state="in_progress",
            step_index=session.current_step_index,
            total_steps=len(TRIAGE_FORM),
            next_step=_next_step_payload(session, clarification=result.clarification),
            disclaimer=disclaimer,
        )

    # --- completed: build the clinician-facing report ---
    if result.kind == "completed":
        with tracer.start_as_current_span("triage.build_report") as span:
            span.set_attribute("triage.unparsed_count", len(session.unparsed_steps))
            report = await build_report(session, locale)
            span.set_attribute("triage.specialist_category", report.specialist_recommendation.category)
        await triage_memory.save_session(session)
        await record_audit_event(
            EVENT_TRIAGE_COMPLETE,
            user_id=user_id,
            conversation_id=session.session_id,
            request_id=get_request_id(),
            unparsed_count=len(session.unparsed_steps),
            red_flags_count=len(report.detected_red_flags),
            specialist_category=report.specialist_recommendation.category,
            locale=locale,
        )
        return TriageAdvanceResponse(
            session_id=session.session_id,
            state="completed",
            step_index=len(TRIAGE_FORM) - 1,
            total_steps=len(TRIAGE_FORM),
            report=report,
            disclaimer=disclaimer,
        )

    # --- kind == "next_step" ---
    await triage_memory.save_session(session)
    await record_audit_event(
        EVENT_TRIAGE_STEP,
        user_id=user_id,
        conversation_id=session.session_id,
        request_id=get_request_id(),
        step_id=current_step.id,
        step_index=session.current_step_index - 1,  # the step we just accepted
        unparsed=current_step.id in session.unparsed_steps,
        locale=locale,
    )
    return TriageAdvanceResponse(
        session_id=session.session_id,
        state="in_progress",
        step_index=session.current_step_index,
        total_steps=len(TRIAGE_FORM),
        next_step=_next_step_payload(session),
        disclaimer=disclaimer,
    )


@router.get(
    "/session/{session_id}",
    response_model=TriageSessionSnapshot,
    summary="Fetch triage session state (no answers)",
    description=(
        "Returns a minimal state snapshot for client recovery — session state, step index, "
        "locale/region, created/updated timestamps, Redis TTL. Intentionally does NOT return "
        "collected answers; those belong in the final report or in audit logs."
    ),
    responses={
        401: {"description": "Missing or invalid authentication."},
        403: {"description": "Session belongs to a different user."},
        404: {"description": "Session not found or expired."},
    },
)
async def get_session(
    session_id: str,
    auth=Depends(auth_guard),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> TriageSessionSnapshot:
    user_id = resolve_user_id(auth, x_user_id)
    set_user_id(user_id)
    set_conversation_id(session_id)

    owner = await triage_memory.get_owner(session_id)
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Access denied to this triage session")

    session = await triage_memory.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Triage session not found or expired")

    ttl = await triage_memory.get_ttl(session_id)
    return TriageSessionSnapshot(
        session_id=session.session_id,
        state=session.state.value,  # type: ignore[arg-type]
        step_index=session.current_step_index,
        total_steps=len(TRIAGE_FORM),
        locale=session.locale,
        region=session.region,
        created_at=session.created_at,
        updated_at=session.updated_at,
        ttl_seconds=ttl,
    )


@router.delete(
    "/session/{session_id}",
    summary="Abandon a triage session",
    description=(
        "Deletes Redis state (session + owner). Idempotent — returns 404 only if nothing "
        "existed to delete. Records an audit event regardless."
    ),
    responses={
        401: {"description": "Missing or invalid authentication."},
        403: {"description": "Session belongs to a different user."},
        404: {"description": "Session not found."},
    },
)
async def abandon_session(
    session_id: str,
    auth=Depends(auth_guard),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> dict[str, Any]:
    user_id = resolve_user_id(auth, x_user_id)
    set_user_id(user_id)
    set_conversation_id(session_id)

    owner = await triage_memory.get_owner(session_id)
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Access denied to this triage session")

    deleted = await triage_memory.delete_session(session_id)
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Triage session not found")

    await record_audit_event(
        EVENT_TRIAGE_ABANDONED,
        user_id=user_id,
        conversation_id=session_id,
        request_id=get_request_id(),
        keys_deleted=deleted,
    )
    return {"deleted": True, "session_id": session_id}
