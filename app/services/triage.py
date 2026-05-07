"""Pre-consultation triage state machine (D.3.a).

Three responsibilities, split so each is unit-testable in isolation:

1. Declarative form  — `TRIAGE_FORM`, a list of `TriageStep` that fixes the
   question order and the per-step validation shape.
2. Pure advance logic — `advance(session, normalized)` mutates the session
   and returns a tagged outcome (`next_step | completed | red_flag_exit |
   clarify`). No I/O.
3. Two thin LLM calls — `normalize_answer` maps a user's free-text reply to
   a structured value + red-flag signal; `build_report` produces the
   doctor-facing summary from the accumulated answers.

Persistence lives in triage_memory.py; auth, rate-limit, tracing, and audit
live in the router. This module is everything else.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Literal, cast

from ..prompts_triage import (
    NORMALIZE_SYSTEM_PROMPTS,
    REPORT_SYSTEM_PROMPTS,
    SPECIALIST_CATEGORIES,
)
from ..schemas_triage import (
    SpecialistRecommendation,
    StructuredAnswers,
    TriageReport,
)
from ..services.i18n import normalize_locale
from ..services.openai_call_guard import openai_call_guard
from ..services.openai_client import client

logger = logging.getLogger(__name__)

MAX_CLARIFICATIONS_PER_STEP = 2
_SPECIALIST_FALLBACK = "gp"


class TriageState(str, Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    RED_FLAG_EXIT = "red_flag_exit"
    ABANDONED = "abandoned"


StepKind = Literal["free_text", "choice", "int_scale", "boolean"]


@dataclass(frozen=True)
class TriageStep:
    id: str
    kind: StepKind
    prompts: dict[str, str]  # locale -> question
    choices: tuple[str, ...] | None = None  # only for kind=="choice"
    int_range: tuple[int, int] | None = None  # only for kind=="int_scale"
    red_flag_check: bool = False  # does the LLM apply red-flag detection on the raw answer?

    def question(self, locale: str) -> str:
        loc = normalize_locale(locale)
        return self.prompts.get(loc, self.prompts["ru"])


# ---- TRIAGE_FORM ----------------------------------------------------------
# Fixed, reviewed, server-authoritative. Order matters; changing it is a
# product/medical decision, not a one-off refactor. Adding a step means
# updating tests/test_triage_form.py floor counts too.

TRIAGE_FORM: tuple[TriageStep, ...] = (
    TriageStep(
        id="primary_complaint",
        kind="free_text",
        prompts={
            "ru": "С чем вы пришли сегодня? Опишите основную жалобу в 1–2 предложениях.",
            "en": "What brings you in today? Describe the main concern in 1–2 sentences.",
            "kk": "Бүгін сізді не алаңдатады? Негізгі шағымды 1–2 сөйлеммен сипаттаңыз.",
        },
        red_flag_check=True,
    ),
    TriageStep(
        id="onset",
        kind="free_text",
        prompts={
            "ru": "Когда это началось? Укажите примерную дату или сколько дней назад.",
            "en": "When did this start? Give a rough date or how many days ago.",
            "kk": "Бұл қашан басталды? Шамамен қашан немесе неше күн бұрын?",
        },
    ),
    TriageStep(
        id="trajectory",
        kind="choice",
        prompts={
            "ru": "Состояние становится хуже, остаётся таким же или улучшается?",
            "en": "Is it getting worse, staying the same, or improving?",
            "kk": "Жағдай нашарлап бара ма, тұрақты ма, әлде жақсарып келе ме?",
        },
        choices=("worsening", "stable", "improving"),
    ),
    TriageStep(
        id="severity",
        kind="int_scale",
        prompts={
            "ru": "По шкале от 1 до 10, насколько это вас беспокоит сейчас? (10 — максимально сильно)",
            "en": "On a scale of 1–10, how much is this bothering you right now? (10 = worst)",
            "kk": "1-ден 10-ға дейінгі шкала бойынша қазір сізді қаншалықты алаңдатады? (10 — ең қатты)",
        },
        int_range=(1, 10),
    ),
    TriageStep(
        id="accompanying",
        kind="free_text",
        prompts={
            "ru": "Какие ещё симптомы вы замечаете (температура, тошнота, слабость и т.д.)?",
            "en": "What other symptoms have you noticed (fever, nausea, weakness, etc.)?",
            "kk": "Тағы қандай симптомдар байқайсыз (қызба, жүрек айну, әлсіздік, т.б.)?",
        },
        red_flag_check=True,
    ),
    TriageStep(
        id="triggers",
        kind="free_text",
        prompts={
            "ru": "Что усиливает или облегчает состояние?",
            "en": "What makes it worse or better?",
            "kk": "Қандай жағдайда нашарлайды немесе жеңілдейді?",
        },
    ),
    TriageStep(
        id="relevant_history",
        kind="free_text",
        prompts={
            "ru": "Есть ли хронические заболевания, похожие эпизоды раньше, недавние поездки или травмы?",
            "en": "Any chronic conditions, similar past episodes, recent travel or injuries?",
            "kk": "Созылмалы аурулар, бұрынғы ұқсас эпизодтар, жақындағы саяхат немесе жарақаттар бар ма?",
        },
    ),
    TriageStep(
        id="current_meds",
        kind="free_text",
        prompts={
            "ru": "Какие лекарства или добавки вы принимаете сейчас?",
            "en": "What medications or supplements are you currently taking?",
            "kk": "Қазір қандай дәрілер немесе қоспалар қабылдайсыз?",
        },
    ),
    TriageStep(
        id="allergies",
        kind="free_text",
        prompts={
            "ru": "Известные аллергии (лекарства, еда, окружающая среда)?",
            "en": "Known allergies (medications, foods, environmental)?",
            "kk": "Белгілі аллергиялар (дәрі-дәрмек, тамақ, қоршаған орта)?",
        },
    ),
    TriageStep(
        id="explicit_red_flags",
        kind="boolean",
        prompts={
            "ru": (
                "Есть ли прямо сейчас что-то из следующего: сильная боль в груди; "
                "затруднённое дыхание; потеря сознания; обильное кровотечение; "
                "суицидальные мысли? (да/нет)"
            ),
            "en": (
                "Right now, do you have any of: severe chest pain; trouble breathing; "
                "loss of consciousness; heavy bleeding; suicidal thoughts? (yes/no)"
            ),
            "kk": (
                "Қазіргі уақытта мыналардың бірі бар ма: кеудедегі қатты ауырсыну; "
                "тыныс алу қиындығы; есінен тану; мол қан кету; суицидтік ойлар? (иә/жоқ)"
            ),
        },
        red_flag_check=True,
    ),
)


def step_by_id(step_id: str) -> TriageStep | None:
    for s in TRIAGE_FORM:
        if s.id == step_id:
            return s
    return None


# ---- Session --------------------------------------------------------------


@dataclass
class TriageSession:
    session_id: str
    user_id: str
    locale: str
    region: str | None
    state: TriageState
    current_step_index: int
    answers: dict[str, Any] = field(default_factory=dict)
    unparsed_steps: list[str] = field(default_factory=list)
    clarification_counts: dict[str, int] = field(default_factory=dict)
    red_flags: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # Optimistic-lock revision (A6). The router increments this on every save;
    # `triage_memory.save_session` rejects writes whose version disagrees with
    # what's currently in Redis — that's the signal that another concurrent
    # request raced us and the caller should reload + retry.
    version: int = 0

    @classmethod
    def new(cls, user_id: str, locale: str, region: str | None) -> TriageSession:
        return cls(
            session_id=str(uuid.uuid4()),
            user_id=user_id,
            locale=normalize_locale(locale),
            region=region,
            state=TriageState.IN_PROGRESS,
            current_step_index=0,
        )

    def to_json(self) -> str:
        payload = asdict(self)
        payload["state"] = self.state.value
        return json.dumps(payload, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> TriageSession:
        data = json.loads(raw)
        data["state"] = TriageState(data["state"])
        # Pre-A6 records have no `version` field; default to 0 so they continue
        # to load and the next save will compare-and-swap from version=0.
        data.setdefault("version", 0)
        return cls(**data)


class TriageConcurrentUpdate(Exception):
    """Raised by triage_memory.save_session when the persisted version doesn't
    match the in-memory `session.version` — i.e. another writer landed first."""


# ---- Normalized answer ----------------------------------------------------


@dataclass(frozen=True)
class NormalizedAnswer:
    """Result of one `normalize_answer` call.

    `value` is None when `clarification_needed` is set (the LLM wants the user
    to restate). When `unparsed` is True, `value` carries the raw user text so
    downstream isn't left with a hole.
    """

    value: Any
    unparsed: bool = False
    red_flag: bool = False
    red_flag_reason: str | None = None
    clarification_needed: str | None = None


# ---- advance (pure) -------------------------------------------------------


@dataclass(frozen=True)
class AdvanceResult:
    """Tagged union for the router to switch on.

    Exactly one of `next_step`, `completed`, `clarify`, `red_flag_exit` is set
    (the others are None/False). Keeping it as one dataclass with discriminator
    fields is easier for tests than a real union type.
    """

    kind: Literal["next_step", "completed", "clarify", "red_flag_exit"]
    clarification: str | None = None  # for kind="clarify"
    red_flag_reason: str | None = None  # for kind="red_flag_exit"


def advance(session: TriageSession, normalized: NormalizedAnswer) -> AdvanceResult:
    """Move the session one step forward based on the normalized answer.

    Mutates the session in place (callers save it afterwards). Always returns
    an `AdvanceResult` — the router uses it to decide what JSON to return and
    whether to invoke `build_report`.
    """
    if session.state is not TriageState.IN_PROGRESS:
        raise ValueError(f"cannot advance session in state {session.state.value}")
    if session.current_step_index >= len(TRIAGE_FORM):
        raise ValueError("session has no more steps but is still in_progress")

    step = TRIAGE_FORM[session.current_step_index]
    session.updated_at = time.time()

    # 1. Red flag short-circuits everything, even if the LLM wanted clarification.
    if step.red_flag_check and normalized.red_flag and normalized.red_flag_reason:
        session.state = TriageState.RED_FLAG_EXIT
        session.red_flags.append(normalized.red_flag_reason)
        return AdvanceResult(kind="red_flag_exit", red_flag_reason=normalized.red_flag_reason)

    # 2. Clarification loop, capped.
    if normalized.clarification_needed and not normalized.unparsed:
        count = session.clarification_counts.get(step.id, 0) + 1
        session.clarification_counts[step.id] = count
        if count < MAX_CLARIFICATIONS_PER_STEP:
            return AdvanceResult(kind="clarify", clarification=normalized.clarification_needed)
        # Cap reached — the LLM still wanted clarification but we won't loop
        # forever. Force-accept as unparsed so the report has *something*.
        session.unparsed_steps.append(step.id)
        session.answers[step.id] = None
        session.current_step_index += 1
        if session.current_step_index >= len(TRIAGE_FORM):
            session.state = TriageState.COMPLETED
            return AdvanceResult(kind="completed")
        return AdvanceResult(kind="next_step")

    # 3. Normal acceptance.
    if normalized.unparsed:
        session.unparsed_steps.append(step.id)
    session.answers[step.id] = normalized.value
    session.current_step_index += 1

    if session.current_step_index >= len(TRIAGE_FORM):
        session.state = TriageState.COMPLETED
        return AdvanceResult(kind="completed")
    return AdvanceResult(kind="next_step")


# ---- LLM: normalize_answer -----------------------------------------------


def _normalize_user_message(step: TriageStep, raw_answer: str) -> str:
    """Build the user-role content for the normalize call.

    Embeds the step metadata alongside the raw answer so the LLM can apply
    the kind-specific rules from the system prompt.
    """
    spec: dict[str, Any] = {
        "step_id": step.id,
        "step_kind": step.kind,
        "raw_answer": raw_answer,
    }
    if step.choices is not None:
        spec["allowed_choices"] = list(step.choices)
    if step.int_range is not None:
        spec["range_min"] = step.int_range[0]
        spec["range_max"] = step.int_range[1]
    return json.dumps(spec, ensure_ascii=False)


async def normalize_answer(step: TriageStep, raw_answer: str, locale: str) -> NormalizedAnswer:
    """One LLM call to structure the user's answer + detect emergencies.

    Fails safely: on any exception, returns an unparsed NormalizedAnswer
    carrying the raw text. The alternative — propagating a 5xx from the
    OpenAI client into the user's triage — would be strictly worse UX.
    """
    loc = normalize_locale(locale)
    system_prompt = NORMALIZE_SYSTEM_PROMPTS.get(loc, NORMALIZE_SYSTEM_PROMPTS["ru"])
    user_message = _normalize_user_message(step, raw_answer)

    try:
        from ..config import settings

        async with openai_call_guard():
            resp = await client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.1,
                max_tokens=300,
                response_format=cast(Any, {"type": "json_object"}),
            )
        raw_json = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw_json)
    except Exception:
        logger.exception("triage.normalize failed for step=%s; force-accepting raw", step.id)
        return NormalizedAnswer(value=raw_answer, unparsed=True)

    return _coerce_normalized(data, step, raw_answer)


def _coerce_normalized(data: dict[str, Any], step: TriageStep, raw_answer: str) -> NormalizedAnswer:
    """Map the LLM's JSON dict to a NormalizedAnswer with typing assertions.

    Defensive on purpose — malformed JSON from a jittery model shouldn't
    nuke the session. If the value doesn't fit the step kind, mark unparsed
    and fall through with the raw answer.
    """
    red_flag = bool(data.get("red_flag", False))
    red_flag_reason = data.get("red_flag_reason") or None
    clarification = data.get("clarification_needed") or None
    unparsed = bool(data.get("unparsed", False))
    value = data.get("value")

    if clarification and not unparsed:
        return NormalizedAnswer(
            value=None,
            clarification_needed=str(clarification)[:240],
            red_flag=red_flag,
            red_flag_reason=red_flag_reason,
        )

    coerced, ok = _coerce_value_for_kind(value, step)
    if not ok:
        return NormalizedAnswer(
            value=raw_answer,
            unparsed=True,
            red_flag=red_flag,
            red_flag_reason=red_flag_reason,
        )

    return NormalizedAnswer(
        value=coerced,
        unparsed=unparsed,
        red_flag=red_flag,
        red_flag_reason=red_flag_reason,
    )


def _coerce_value_for_kind(value: Any, step: TriageStep) -> tuple[Any, bool]:
    """Enforce the step's kind contract on the LLM-returned value."""
    if value is None:
        return None, False

    if step.kind == "free_text":
        if isinstance(value, str) and value.strip():
            return value.strip()[:240], True
        return None, False

    if step.kind == "choice":
        if isinstance(value, str) and step.choices and value in step.choices:
            return value, True
        return None, False

    if step.kind == "int_scale":
        try:
            intval = int(value)
        except (TypeError, ValueError):
            return None, False
        if step.int_range is None:
            return intval, True
        lo, hi = step.int_range
        if lo <= intval <= hi:
            return intval, True
        return None, False

    if step.kind == "boolean":
        if isinstance(value, bool):
            return value, True
        if isinstance(value, str):
            truthy = {"true", "yes", "да", "иә"}
            falsy = {"false", "no", "нет", "жоқ"}
            lowered = value.strip().lower()
            if lowered in truthy:
                return True, True
            if lowered in falsy:
                return False, True
        return None, False

    return None, False


# ---- LLM: build_report ----------------------------------------------------


_STEP_TO_STRUCTURED_FIELD: dict[str, str] = {
    "primary_complaint": "primary_complaint",
    "onset": "onset",
    "trajectory": "trajectory",
    "severity": "severity",
    "accompanying": "accompanying",
    "triggers": "triggers",
    "relevant_history": "relevant_history",
    "current_meds": "current_meds",
    "allergies": "allergies",
    "explicit_red_flags": "explicit_red_flags",
}


def _structured_from_answers(answers: dict[str, Any]) -> StructuredAnswers:
    payload: dict[str, Any] = {}
    for step_id, field_name in _STEP_TO_STRUCTURED_FIELD.items():
        if step_id in answers:
            payload[field_name] = answers[step_id]
    return StructuredAnswers(**payload)


async def build_report(session: TriageSession, locale: str) -> TriageReport:
    """Second LLM call: produce the clinician-facing report.

    Falls back to a rule-assembled report when the LLM is unavailable or
    returns malformed JSON — a valid structured report with empty summary
    is strictly more useful than a 500 in the middle of the triage flow.
    """
    loc = normalize_locale(locale)
    system_prompt = REPORT_SYSTEM_PROMPTS.get(loc, REPORT_SYSTEM_PROMPTS["ru"])
    structured = _structured_from_answers(session.answers)
    user_message = json.dumps(
        {
            "answers": session.answers,
            "unparsed_steps": session.unparsed_steps,
            "red_flags_noted": session.red_flags,
        },
        ensure_ascii=False,
    )

    try:
        from ..config import settings

        async with openai_call_guard():
            resp = await client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.2,
                max_tokens=600,
                response_format=cast(Any, {"type": "json_object"}),
            )
        data = json.loads((resp.choices[0].message.content or "").strip())
    except Exception:
        logger.exception("triage.build_report failed; returning fallback report")
        return _fallback_report(structured, session)

    return _coerce_report(data, structured, session)


def _coerce_report(
    data: dict[str, Any],
    structured: StructuredAnswers,
    session: TriageSession,
) -> TriageReport:
    summary = str(data.get("clinical_summary") or "").strip()[:2000]
    rec = data.get("specialist_recommendation") or {}
    category = str(rec.get("category") or "").strip()
    if category not in SPECIALIST_CATEGORIES:
        logger.info("triage.build_report: category %r out of enum → fallback %s", category, _SPECIALIST_FALLBACK)
        category = _SPECIALIST_FALLBACK
    rationale = str(rec.get("rationale") or "").strip()[:500]

    raw_flags = data.get("detected_red_flags") or []
    if isinstance(raw_flags, list):
        detected_flags = [str(f).strip()[:240] for f in raw_flags if str(f).strip()]
    else:
        detected_flags = []
    # Merge in session-level red flags so nothing is lost if the LLM dropped them.
    for reason in session.red_flags:
        if reason not in detected_flags:
            detected_flags.append(reason)

    return TriageReport(
        clinical_summary=summary or _fallback_summary(structured, session.locale),
        structured=structured,
        specialist_recommendation=SpecialistRecommendation(
            category=category,
            rationale=rationale or _fallback_rationale(session.locale),
        ),
        detected_red_flags=detected_flags,
    )


def _fallback_report(structured: StructuredAnswers, session: TriageSession) -> TriageReport:
    return TriageReport(
        clinical_summary=_fallback_summary(structured, session.locale),
        structured=structured,
        specialist_recommendation=SpecialistRecommendation(
            category=_SPECIALIST_FALLBACK,
            rationale=_fallback_rationale(session.locale),
        ),
        detected_red_flags=list(session.red_flags),
    )


def _fallback_summary(structured: StructuredAnswers, locale: str) -> str:
    loc = normalize_locale(locale)
    parts: list[str] = []
    if structured.primary_complaint:
        parts.append(str(structured.primary_complaint))
    if structured.severity is not None:
        if loc == "en":
            parts.append(f"Severity {structured.severity}/10.")
        elif loc == "kk":
            parts.append(f"Қарқындылығы {structured.severity}/10.")
        else:
            parts.append(f"Интенсивность {structured.severity}/10.")
    if not parts:
        fallback = {
            "ru": "Данные триажа собраны, детали см. в structured.",
            "en": "Triage data collected; see structured for details.",
            "kk": "Триаж деректері жиналды, егжей-тегжейі structured ішінде.",
        }
        return fallback.get(loc, fallback["ru"])
    return " ".join(parts)


def _fallback_rationale(locale: str) -> str:
    loc = normalize_locale(locale)
    rationales = {
        "ru": "Первичная оценка — для триажа направлять к врачу общей практики.",
        "en": "Default routing to a general practitioner for initial clinical assessment.",
        "kk": "Бастапқы бағалау үшін жалпы тәжірибелі дәрігерге жіберу.",
    }
    return rationales.get(loc, rationales["ru"])
