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
    EVENT_TRIAGE_INJECTION_BLOCKED,
    EVENT_TRIAGE_RED_FLAG_EXIT,
    EVENT_TRIAGE_SENSITIVE_BLOCKED,
    EVENT_TRIAGE_START,
    EVENT_TRIAGE_STEP,
    record_audit_event,
)
from ..services.content_safety import SENSITIVE_REFUSAL, detect_sensitive_topic
from ..services.i18n import get_disclaimer, get_emergency_phone, normalize_locale
from ..services.rate_limit import enforce_rate_limit
from ..services.safety import INJECTION_REFUSAL, detect_injection
from ..services.triage import (
    TRIAGE_FORM,
    TriageConcurrentUpdate,
    TriageSession,
    TriageState,
    advance,
    build_report,
    normalize_answer,
)
from ..services.triage_redflags import keyword_red_flag
from ..tracing import tracer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/triage", tags=["triage"])


_ADVANCE_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"description": "Missing `answer` for an existing session, or step_index out of range."},
    401: {"description": "Missing or invalid authentication."},
    403: {"description": "Session belongs to a different user."},
    404: {"description": "Session not found or expired."},
    409: {
        "description": (
            "Session already terminated (red_flag_exit or completed) — start a new one. "
            "Also returned when a concurrent advance landed first; the client should "
            "reload the session and retry."
        )
    },
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


async def _save_or_409(session: TriageSession) -> None:
    """Persist `session` and translate version conflicts into HTTP 409.

    A6: two parallel advances on the same `session_id` would otherwise both
    succeed; one would silently overwrite the other (chat is RPUSH so it
    serializes; triage stores a JSON blob so it doesn't). The CAS in
    triage_memory.save_session rejects the loser; the router surfaces that
    as 409 so the client can reload + retry instead of getting confused
    state.
    """
    try:
        await triage_memory.save_session(session)
    except TriageConcurrentUpdate as e:
        raise HTTPException(
            status_code=409,
            detail="Concurrent triage update detected; reload the session and retry",
        ) from e


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
    # `session` was bound to a TriageSession in the create branch above, so we
    # can't re-annotate here; rebind via a fresh local and assign back after the
    # None check so the rest of the function sees a non-Optional type.
    loaded = await triage_memory.load_session(req.session_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail="Triage session not found or expired")
    session = loaded

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

    # S3: prompt-injection guard. Triage answers are free text passed straight
    # into an LLM (`normalize_answer`); without this gate a hostile user can
    # try the same "ignore previous instructions" tricks the chat router blocks.
    # We don't advance the session — caller can retry the same step with a
    # cleaner answer. Audit-only event so an operator can spot-check.
    if detect_injection(req.answer):
        await record_audit_event(
            EVENT_TRIAGE_INJECTION_BLOCKED,
            user_id=user_id,
            conversation_id=session.session_id,
            request_id=get_request_id(),
            step_id=current_step.id,
            step_index=session.current_step_index,
            locale=locale,
        )
        return TriageAdvanceResponse(
            session_id=session.session_id,
            state="in_progress",
            step_index=session.current_step_index,
            total_steps=len(TRIAGE_FORM),
            next_step=_next_step_payload(
                session,
                clarification=INJECTION_REFUSAL.get(locale, INJECTION_REFUSAL["ru"]),
            ),
            disclaimer=disclaimer,
        )

    # Sensitive-topic policy block. Triage has no intent classifier, so the
    # regex pre-gate is the only line of defense for this endpoint. We
    # re-prompt the same step (no advance) — same UX as the injection block.
    sensitive = detect_sensitive_topic(req.answer)
    if sensitive is not None:
        await record_audit_event(
            EVENT_TRIAGE_SENSITIVE_BLOCKED,
            user_id=user_id,
            conversation_id=session.session_id,
            request_id=get_request_id(),
            step_id=current_step.id,
            step_index=session.current_step_index,
            locale=locale,
            sensitive_category=sensitive.category,
            locale_hit=sensitive.locale_hit,
            pattern_id=sensitive.pattern_id,
        )
        return TriageAdvanceResponse(
            session_id=session.session_id,
            state="in_progress",
            step_index=session.current_step_index,
            total_steps=len(TRIAGE_FORM),
            next_step=_next_step_payload(
                session,
                clarification=SENSITIVE_REFUSAL.get(locale, SENSITIVE_REFUSAL["ru"]),
            ),
            disclaimer=disclaimer,
        )

    with tracer.start_as_current_span("triage.normalize") as span:
        span.set_attribute("triage.step_id", current_step.id)
        span.set_attribute("triage.step_index", session.current_step_index)
        normalized = await normalize_answer(current_step, req.answer, locale)
        span.set_attribute("triage.red_flag", bool(normalized.red_flag))
        span.set_attribute("triage.unparsed", bool(normalized.unparsed))

    # S3: belt-and-suspenders red-flag check. The LLM in `normalize_answer`
    # already handles the obvious cases, but a deterministic keyword pass
    # catches the few hostile or confused inputs where the LLM might
    # under-flag (e.g. answering "no, I'm fine" while describing actual chest
    # pain). Only runs on steps already marked red_flag_check.
    if current_step.red_flag_check and not normalized.red_flag:
        kw = keyword_red_flag(req.answer, locale)
        if kw:
            from ..services.triage import NormalizedAnswer

            normalized = NormalizedAnswer(
                value=normalized.value,
                unparsed=normalized.unparsed,
                red_flag=True,
                red_flag_reason=kw,
                clarification_needed=normalized.clarification_needed,
            )

    result = advance(session, normalized)

    # --- red flag exit ---
    if result.kind == "red_flag_exit":
        await _save_or_409(session)
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
        await _save_or_409(session)
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
        await _save_or_409(session)
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
    await _save_or_409(session)
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
        state=session.state.value,
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
