"""Pydantic schemas for the pre-consultation triage pipeline (D.3.a).

Split from app/schemas.py the same way app/schemas_articles.py is split — the
chat surface and the triage surface evolve independently and merging them
into one file invites accidental coupling.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .prompts_triage import SPECIALIST_CATEGORIES

_UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
_REGION_PATTERN = r"^[A-Za-z]{2}$"


class TriageAdvanceRequest(BaseModel):
    session_id: str | None = Field(
        default=None,
        max_length=36,
        pattern=_UUID_PATTERN,
        description=(
            "UUIDv4 of the triage session. Omit on the first request — the server "
            "creates one and returns it in the response. Ownership is locked on the "
            "first write (same pattern as /v1/chat conversation_id)."
        ),
    )
    answer: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
        description=(
            "User's free-text answer to the current step's question. "
            "Required when `session_id` is supplied; ignored on session creation."
        ),
    )
    locale: str = Field(
        default="ru",
        description="Response language. Supported: 'ru', 'en', 'kk'. Unknown values fold to 'ru'.",
    )
    region: str | None = Field(
        default=None,
        pattern=_REGION_PATTERN,
        description=(
            "Optional ISO 3166-1 alpha-2 country code. Used only if a red flag fires — "
            "`get_emergency_phone(region, locale)` picks the right emergency number."
        ),
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"locale": "ru", "region": "KZ"},
                {
                    "session_id": "c3a1b2d4-5678-4abc-9def-0123456789ab",
                    "answer": "Болит голова уже три дня, пульсирующая, справа.",
                    "locale": "ru",
                    "region": "KZ",
                },
            ]
        }
    )


class StructuredAnswers(BaseModel):
    """The final shape of normalized answers carried into the report.

    Every field is optional because (a) a triage can end early on red-flag exit
    and (b) a force-accepted unparsed answer stays as a raw string rather than
    the ideal structured type.
    """

    primary_complaint: str | None = None
    onset: str | None = None
    trajectory: Literal["worsening", "stable", "improving"] | str | None = None
    severity: int | None = Field(default=None, ge=0, le=10)
    accompanying: str | None = None
    triggers: str | None = None
    relevant_history: str | None = None
    current_meds: str | None = None
    allergies: str | None = None
    explicit_red_flags: bool | None = None


class SpecialistRecommendation(BaseModel):
    category: str = Field(
        ...,
        description=(
            "Closed enum: one of "
            + ", ".join(SPECIALIST_CATEGORIES)
            + ". Defaults to `gp` if the LLM returns an out-of-list value."
        ),
    )
    rationale: str = Field(..., max_length=500, description="One-sentence reason for the recommendation.")


class TriageReport(BaseModel):
    clinical_summary: str = Field(..., description="3–5 sentence clinician-facing summary in the session locale.")
    structured: StructuredAnswers = Field(..., description="Normalized answers, field-addressable.")
    specialist_recommendation: SpecialistRecommendation
    detected_red_flags: list[str] = Field(default_factory=list, description="Red-flag reasons noted during triage.")


class TriageAdvanceResponse(BaseModel):
    """Single response shape covering every router outcome.

    `state` discriminates: in_progress → use `next_step`; completed → use `report`;
    red_flag_exit → use `emergency_message` + `detected_red_flag`. This keeps the
    client code simple — one JSON body, one switch on `state`.
    """

    session_id: str
    state: Literal["in_progress", "completed", "red_flag_exit"]
    step_index: int = Field(
        ..., description="Zero-based index of the step the server is currently waiting on (or the last one seen)."
    )
    total_steps: int = Field(..., description="Total number of steps in the triage form.")
    next_step: dict[str, Any] | None = Field(
        default=None,
        description=(
            "When state=in_progress: `{step_id, question, kind, choices?, range?, clarification?}`. "
            "`clarification` is set when the server wants the user to restate the previous answer. "
            "Null otherwise."
        ),
    )
    report: TriageReport | None = Field(
        default=None,
        description="Present only when state=completed.",
    )
    emergency_message: str | None = Field(
        default=None,
        description="Present only when state=red_flag_exit. Locale-specific, with the emergency number interpolated.",
    )
    detected_red_flag: str | None = Field(
        default=None,
        description="Short description of the flag that triggered the exit. Present only when state=red_flag_exit.",
    )
    emergency_phone: str | None = Field(
        default=None,
        description="The resolved phone number (D.1 logic). Present only when state=red_flag_exit.",
    )
    disclaimer: str = Field(..., description="Standard medical disclaimer for the requested locale.")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "session_id": "c3a1b2d4-5678-4abc-9def-0123456789ab",
                    "state": "in_progress",
                    "step_index": 0,
                    "total_steps": 10,
                    "next_step": {
                        "step_id": "primary_complaint",
                        "question": "С чем вы пришли сегодня? Опишите основную жалобу.",
                        "kind": "free_text",
                    },
                    "disclaimer": "Это не медицинский диагноз и не замена консультации врача.",
                },
                {
                    "session_id": "c3a1b2d4-5678-4abc-9def-0123456789ab",
                    "state": "red_flag_exit",
                    "step_index": 0,
                    "total_steps": 10,
                    "emergency_message": "Обнаружены признаки неотложного состояния. Немедленно вызовите скорую помощь (112 или 103)…",
                    "detected_red_flag": "Сильная боль в груди с отдачей в руку",
                    "emergency_phone": "112 или 103",
                    "disclaimer": "Это не медицинский диагноз и не замена консультации врача.",
                },
            ]
        }
    )


class TriageSessionSnapshot(BaseModel):
    """Response shape for GET /v1/triage/session/{id} — recovery view.

    Intentionally does NOT include raw answers; those belong in the final
    report and in audit logs, not in a free-form recovery endpoint.
    """

    session_id: str
    state: Literal["in_progress", "completed", "red_flag_exit", "abandoned"]
    step_index: int
    total_steps: int
    locale: str
    region: str | None = None
    created_at: float
    updated_at: float
    ttl_seconds: int = Field(..., description="Remaining Redis TTL in seconds, or -1 when the key has no expiry.")
