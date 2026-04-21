"""Pure state-machine tests for D.3.a triage.

No network, no Redis — drives `advance()` with pre-built NormalizedAnswer
values and asserts the session transitions. Separate from LLM tests
(test_triage_normalize_coercion) so logic failures aren't masked by
JSON-coercion edge cases.
"""

from __future__ import annotations

import pytest

from app.services.triage import (
    MAX_CLARIFICATIONS_PER_STEP,
    TRIAGE_FORM,
    NormalizedAnswer,
    TriageSession,
    TriageState,
    TriageStep,
    _coerce_value_for_kind,
    _fallback_report,
    advance,
    step_by_id,
)


def _session(user_id: str = "u1", locale: str = "ru") -> TriageSession:
    return TriageSession.new(user_id=user_id, locale=locale, region=None)


def _drive_valid(session: TriageSession, value: object = "some answer") -> None:
    """Feed a plain, non-red-flag, non-unparsed answer to the current step."""
    advance(session, NormalizedAnswer(value=value))


# --------------- form structure ---------------


class TestTriageForm:
    def test_form_has_expected_step_count(self):
        # Adding a step is a product decision; update this floor with intent.
        assert len(TRIAGE_FORM) == 10

    def test_step_ids_are_unique(self):
        ids = [s.id for s in TRIAGE_FORM]
        assert len(ids) == len(set(ids))

    def test_every_step_has_three_locales(self):
        for step in TRIAGE_FORM:
            assert set(step.prompts.keys()) == {"ru", "en", "kk"}, f"{step.id} locales: {step.prompts.keys()}"

    def test_choice_steps_have_choices(self):
        for step in TRIAGE_FORM:
            if step.kind == "choice":
                assert step.choices, f"{step.id} is a choice step without choices"

    def test_int_scale_steps_have_range(self):
        for step in TRIAGE_FORM:
            if step.kind == "int_scale":
                assert step.int_range, f"{step.id} is int_scale without range"

    def test_step_by_id(self):
        assert step_by_id("primary_complaint") is TRIAGE_FORM[0]
        assert step_by_id("does_not_exist") is None

    def test_question_falls_back_to_ru_for_unknown_locale(self):
        step = TRIAGE_FORM[0]
        # normalize_locale folds 'de' to 'ru'
        assert step.question("de") == step.prompts["ru"]


# --------------- advance: happy path ---------------


class TestAdvanceHappyPath:
    def test_valid_answer_advances_one_step(self):
        s = _session()
        assert s.current_step_index == 0
        result = advance(s, NormalizedAnswer(value="head hurts"))
        assert result.kind == "next_step"
        assert s.current_step_index == 1
        assert s.answers["primary_complaint"] == "head hurts"

    def test_full_traversal_ends_in_completed(self):
        s = _session()
        # 10 steps, 10 advances — last returns "completed", not "next_step".
        for i in range(len(TRIAGE_FORM)):
            step = TRIAGE_FORM[i]
            value: object
            if step.kind == "int_scale":
                value = 3
            elif step.kind == "boolean":
                value = False
            elif step.kind == "choice":
                value = step.choices[0]  # type: ignore[index]
            else:
                value = f"answer_{i}"
            result = advance(s, NormalizedAnswer(value=value))
            if i < len(TRIAGE_FORM) - 1:
                assert result.kind == "next_step", f"step {i} should have been next_step"
            else:
                assert result.kind == "completed"
        assert s.state is TriageState.COMPLETED
        assert len(s.answers) == len(TRIAGE_FORM)

    def test_timestamps_update_on_advance(self):
        s = _session()
        # Plant an obviously-stale sentinel so the comparison isn't at the mercy
        # of time.time() resolution on fast machines (advance can run within the
        # same microsecond as TriageSession.new).
        s.updated_at = 0.0
        advance(s, NormalizedAnswer(value="x"))
        assert s.updated_at > 0.0


# --------------- advance: red-flag exit ---------------


class TestAdvanceRedFlag:
    def test_red_flag_on_red_flag_step_exits(self):
        s = _session()
        # primary_complaint is step 0 and has red_flag_check=True.
        assert TRIAGE_FORM[0].red_flag_check is True
        result = advance(
            s,
            NormalizedAnswer(
                value="severe chest pain radiating to arm",
                red_flag=True,
                red_flag_reason="Severe chest pain with radiation",
            ),
        )
        assert result.kind == "red_flag_exit"
        assert result.red_flag_reason == "Severe chest pain with radiation"
        assert s.state is TriageState.RED_FLAG_EXIT
        assert "Severe chest pain with radiation" in s.red_flags

    def test_red_flag_on_non_red_flag_step_ignored(self):
        """Red flag is checked only on steps with red_flag_check=True — other
        steps accept the answer and move on (the LLM may misfire)."""
        s = _session()
        _drive_valid(s)  # advance past primary_complaint
        _drive_valid(s)  # onset — not a red-flag step
        # Now on 'trajectory' — also not a red-flag step.
        assert TRIAGE_FORM[s.current_step_index].id == "trajectory"
        assert TRIAGE_FORM[s.current_step_index].red_flag_check is False
        result = advance(
            s,
            NormalizedAnswer(
                value="worsening",
                red_flag=True,
                red_flag_reason="LLM misfire",
            ),
        )
        # Advanced normally; red_flag suppressed because step doesn't opt in.
        assert result.kind == "next_step"
        assert s.state is TriageState.IN_PROGRESS
        assert "LLM misfire" not in s.red_flags

    def test_advance_on_terminated_session_raises(self):
        s = _session()
        s.state = TriageState.RED_FLAG_EXIT
        with pytest.raises(ValueError, match="red_flag_exit"):
            advance(s, NormalizedAnswer(value="x"))


# --------------- advance: clarification loop ---------------


class TestAdvanceClarification:
    def test_first_clarification_stays_on_step(self):
        s = _session()
        result = advance(
            s,
            NormalizedAnswer(value=None, clarification_needed="Когда именно?"),
        )
        assert result.kind == "clarify"
        assert result.clarification == "Когда именно?"
        assert s.current_step_index == 0  # did NOT advance
        assert s.clarification_counts["primary_complaint"] == 1

    def test_second_clarification_force_accepts_as_unparsed(self):
        s = _session()
        # First clarification
        advance(s, NormalizedAnswer(value=None, clarification_needed="?"))
        assert s.current_step_index == 0
        # Second clarification — hits the cap MAX_CLARIFICATIONS_PER_STEP=2.
        assert MAX_CLARIFICATIONS_PER_STEP == 2
        result = advance(s, NormalizedAnswer(value=None, clarification_needed="??"))
        assert result.kind == "next_step"
        assert s.current_step_index == 1
        assert "primary_complaint" in s.unparsed_steps
        assert s.answers["primary_complaint"] is None

    def test_unparsed_answer_records_step_and_advances(self):
        s = _session()
        result = advance(
            s,
            NormalizedAnswer(value="raw user text", unparsed=True),
        )
        assert result.kind == "next_step"
        assert "primary_complaint" in s.unparsed_steps
        assert s.answers["primary_complaint"] == "raw user text"


# --------------- _coerce_value_for_kind ---------------


class TestCoerceValue:
    def test_free_text_accepts_non_empty_string(self):
        step = TriageStep(id="x", kind="free_text", prompts={"ru": "", "en": "", "kk": ""})
        assert _coerce_value_for_kind("  hi  ", step) == ("hi", True)
        assert _coerce_value_for_kind("", step) == (None, False)
        assert _coerce_value_for_kind(None, step) == (None, False)

    def test_free_text_truncates_at_240(self):
        step = TriageStep(id="x", kind="free_text", prompts={"ru": "", "en": "", "kk": ""})
        long = "a" * 500
        val, ok = _coerce_value_for_kind(long, step)
        assert ok is True
        assert isinstance(val, str) and len(val) == 240

    def test_choice_must_match_exactly(self):
        step = TriageStep(
            id="x",
            kind="choice",
            prompts={"ru": "", "en": "", "kk": ""},
            choices=("worsening", "stable", "improving"),
        )
        assert _coerce_value_for_kind("worsening", step) == ("worsening", True)
        assert _coerce_value_for_kind("getting worse", step) == (None, False)

    def test_int_scale_rejects_out_of_range(self):
        step = TriageStep(
            id="x",
            kind="int_scale",
            prompts={"ru": "", "en": "", "kk": ""},
            int_range=(1, 10),
        )
        assert _coerce_value_for_kind(5, step) == (5, True)
        assert _coerce_value_for_kind("7", step) == (7, True)
        assert _coerce_value_for_kind(0, step) == (None, False)
        assert _coerce_value_for_kind(11, step) == (None, False)
        assert _coerce_value_for_kind("not a number", step) == (None, False)

    def test_boolean_accepts_localized_yes_no(self):
        step = TriageStep(id="x", kind="boolean", prompts={"ru": "", "en": "", "kk": ""})
        assert _coerce_value_for_kind(True, step) == (True, True)
        assert _coerce_value_for_kind(False, step) == (False, True)
        assert _coerce_value_for_kind("yes", step) == (True, True)
        assert _coerce_value_for_kind("да", step) == (True, True)
        assert _coerce_value_for_kind("иә", step) == (True, True)
        assert _coerce_value_for_kind("no", step) == (False, True)
        assert _coerce_value_for_kind("нет", step) == (False, True)
        assert _coerce_value_for_kind("жоқ", step) == (False, True)
        assert _coerce_value_for_kind("maybe", step) == (None, False)


# --------------- fallback report ---------------


class TestFallbackReport:
    def test_fallback_returns_gp_and_fills_summary(self):
        s = _session(locale="en")
        s.answers["primary_complaint"] = "persistent headache"
        s.answers["severity"] = 6
        from app.services.triage import _structured_from_answers

        structured = _structured_from_answers(s.answers)
        report = _fallback_report(structured, s)
        assert report.specialist_recommendation.category == "gp"
        assert "6/10" in report.clinical_summary
        assert "headache" in report.clinical_summary

    def test_fallback_preserves_session_red_flags(self):
        s = _session()
        s.red_flags = ["Severe chest pain", "Vomiting blood"]
        from app.services.triage import _structured_from_answers

        report = _fallback_report(_structured_from_answers(s.answers), s)
        assert report.detected_red_flags == ["Severe chest pain", "Vomiting blood"]
