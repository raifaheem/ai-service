"""Direct unit tests for triage._coerce_normalized / _coerce_report / _fallback_summary (M10c).

Pre-M10 these functions ran only inside the LLM seam (mocked at the router level
in test_triage_router.py), so the malformed-JSON / out-of-enum / range-violation
branches were uncovered. We call them directly here with hand-built dicts.
"""

import pytest

from app.services.triage import (
    TRIAGE_FORM,
    NormalizedAnswer,
    TriageSession,
    TriageState,
    _coerce_normalized,
    _coerce_report,
    _coerce_value_for_kind,
    _fallback_report,
    _fallback_summary,
    _structured_from_answers,
)


def _step(step_id: str):
    return next(s for s in TRIAGE_FORM if s.id == step_id)


def _session(**overrides) -> TriageSession:
    base = {
        "session_id": "sid-1",
        "user_id": "u-1",
        "locale": "ru",
        "region": "KZ",
        "state": TriageState.IN_PROGRESS,
        "current_step_index": 0,
        "answers": {},
    }
    base.update(overrides)
    return TriageSession(**base)


# --------------- _coerce_value_for_kind ---------------


class TestCoerceValueForKind:
    def test_free_text_strips_and_truncates(self):
        out, ok = _coerce_value_for_kind("  hello  ", _step("primary_complaint"))
        assert ok and out == "hello"

    def test_free_text_rejects_empty(self):
        out, ok = _coerce_value_for_kind("   ", _step("primary_complaint"))
        assert not ok and out is None

    def test_free_text_rejects_non_string(self):
        out, ok = _coerce_value_for_kind(123, _step("primary_complaint"))
        assert not ok

    def test_choice_accepts_listed_value(self):
        out, ok = _coerce_value_for_kind("worsening", _step("trajectory"))
        assert ok and out == "worsening"

    def test_choice_rejects_unlisted_value(self):
        out, ok = _coerce_value_for_kind("plateau", _step("trajectory"))
        assert not ok

    def test_int_scale_accepts_in_range(self):
        out, ok = _coerce_value_for_kind(7, _step("severity"))
        assert ok and out == 7

    def test_int_scale_accepts_string_int(self):
        out, ok = _coerce_value_for_kind("8", _step("severity"))
        assert ok and out == 8

    def test_int_scale_rejects_out_of_range(self):
        out, ok = _coerce_value_for_kind(15, _step("severity"))
        assert not ok

    def test_int_scale_rejects_non_numeric(self):
        out, ok = _coerce_value_for_kind("not a number", _step("severity"))
        assert not ok

    def test_boolean_accepts_actual_bool(self):
        out, ok = _coerce_value_for_kind(True, _step("explicit_red_flags"))
        assert ok and out is True

    @pytest.mark.parametrize("yes_word", ["true", "yes", "да", "иә", "Да", "  YES  "])
    def test_boolean_accepts_truthy_strings(self, yes_word):
        out, ok = _coerce_value_for_kind(yes_word, _step("explicit_red_flags"))
        assert ok and out is True

    @pytest.mark.parametrize("no_word", ["false", "no", "нет", "жоқ"])
    def test_boolean_accepts_falsy_strings(self, no_word):
        out, ok = _coerce_value_for_kind(no_word, _step("explicit_red_flags"))
        assert ok and out is False

    def test_boolean_rejects_unknown_string(self):
        out, ok = _coerce_value_for_kind("maybe", _step("explicit_red_flags"))
        assert not ok

    def test_none_value_rejected(self):
        out, ok = _coerce_value_for_kind(None, _step("primary_complaint"))
        assert not ok


# --------------- _coerce_normalized ---------------


class TestCoerceNormalized:
    def test_well_formed_response(self):
        data = {"value": "headache for 2 days", "red_flag": False}
        result = _coerce_normalized(data, _step("primary_complaint"), raw_answer="raw")
        assert isinstance(result, NormalizedAnswer)
        assert result.value == "headache for 2 days"
        assert result.unparsed is False
        assert result.red_flag is False

    def test_clarification_short_circuit(self):
        """When the LLM asks for clarification, return that without coercing value."""
        data = {"clarification_needed": "Could you describe the pain location?"}
        result = _coerce_normalized(data, _step("primary_complaint"), raw_answer="vague")
        assert result.clarification_needed == "Could you describe the pain location?"
        assert result.value is None

    def test_invalid_value_falls_back_to_raw(self):
        """Out-of-enum choice → unparsed=True, value carries raw user text."""
        data = {"value": "plateau"}  # not in trajectory choices
        result = _coerce_normalized(data, _step("trajectory"), raw_answer="kind of stable")
        assert result.unparsed is True
        assert result.value == "kind of stable"

    def test_red_flag_with_reason_propagated(self):
        data = {
            "value": "chest pain",
            "red_flag": True,
            "red_flag_reason": "Chest pain radiating to arm",
        }
        result = _coerce_normalized(data, _step("primary_complaint"), raw_answer="raw")
        assert result.red_flag is True
        assert result.red_flag_reason == "Chest pain radiating to arm"

    def test_severity_out_of_range_marks_unparsed(self):
        data = {"value": 99}
        result = _coerce_normalized(data, _step("severity"), raw_answer="really bad")
        assert result.unparsed is True
        assert result.value == "really bad"

    def test_missing_value_marks_unparsed(self):
        data = {"red_flag": False}
        result = _coerce_normalized(data, _step("primary_complaint"), raw_answer="hmm")
        assert result.unparsed is True
        assert result.value == "hmm"

    def test_truncates_long_text(self):
        long = "x" * 1000
        data = {"value": long}
        result = _coerce_normalized(data, _step("primary_complaint"), raw_answer=long)
        assert len(result.value) <= 240


# --------------- _coerce_report ---------------


class TestCoerceReport:
    def test_well_formed_report(self):
        structured = _structured_from_answers({"primary_complaint": "headache"})
        session = _session(answers={"primary_complaint": "headache"}, red_flags=[])
        data = {
            "clinical_summary": "3-day headache",
            "specialist_recommendation": {
                "category": "neurologist",
                "rationale": "Recurrent headache pattern",
            },
            "detected_red_flags": ["one flag"],
        }
        report = _coerce_report(data, structured, session)
        assert report.clinical_summary == "3-day headache"
        assert report.specialist_recommendation.category == "neurologist"
        assert "one flag" in report.detected_red_flags

    def test_out_of_enum_specialist_falls_back_to_gp(self):
        """Per CLAUDE.md: out-of-enum specialist categories fall back to 'gp'."""
        structured = _structured_from_answers({})
        session = _session(answers={}, red_flags=[])
        data = {
            "clinical_summary": "summary",
            "specialist_recommendation": {"category": "homeopath", "rationale": "?"},
        }
        report = _coerce_report(data, structured, session)
        assert report.specialist_recommendation.category == "gp"

    def test_missing_specialist_falls_back_to_gp(self):
        structured = _structured_from_answers({})
        session = _session(answers={}, red_flags=[])
        data = {"clinical_summary": "summary"}
        report = _coerce_report(data, structured, session)
        assert report.specialist_recommendation.category == "gp"

    def test_session_red_flags_merged(self):
        """Session-level red flags must be preserved even if the LLM drops them."""
        structured = _structured_from_answers({})
        session = _session(answers={}, red_flags=["session-noted flag"])
        data = {
            "clinical_summary": "s",
            "specialist_recommendation": {"category": "gp"},
            "detected_red_flags": ["llm-noted"],
        }
        report = _coerce_report(data, structured, session)
        assert "llm-noted" in report.detected_red_flags
        assert "session-noted flag" in report.detected_red_flags

    def test_empty_summary_uses_fallback(self):
        structured = _structured_from_answers({"primary_complaint": "fatigue"})
        session = _session(answers={"primary_complaint": "fatigue"}, red_flags=[])
        data = {"clinical_summary": "", "specialist_recommendation": {"category": "gp"}}
        report = _coerce_report(data, structured, session)
        assert report.clinical_summary  # non-empty
        assert "fatigue" in report.clinical_summary

    def test_non_list_red_flags_normalized(self):
        """If the LLM returns red_flags as a non-list, fall back to []."""
        structured = _structured_from_answers({})
        session = _session(answers={}, red_flags=[])
        data = {
            "clinical_summary": "s",
            "specialist_recommendation": {"category": "gp"},
            "detected_red_flags": "not a list",
        }
        report = _coerce_report(data, structured, session)
        assert report.detected_red_flags == []


# --------------- _fallback_summary / _fallback_report ---------------


class TestFallbacks:
    def test_fallback_summary_ru(self):
        structured = _structured_from_answers(
            {"primary_complaint": "головная боль", "severity": 7}
        )
        out = _fallback_summary(structured, "ru")
        assert "головная боль" in out
        assert "7/10" in out
        assert "Интенсивность" in out

    def test_fallback_summary_en(self):
        structured = _structured_from_answers({"primary_complaint": "headache", "severity": 5})
        out = _fallback_summary(structured, "en")
        assert "headache" in out
        assert "Severity 5/10" in out

    def test_fallback_summary_kk(self):
        structured = _structured_from_answers({"primary_complaint": "бас ауырады", "severity": 6})
        out = _fallback_summary(structured, "kk")
        assert "Қарқындылығы" in out
        assert "6/10" in out

    def test_fallback_summary_unknown_locale_folds_to_ru(self):
        structured = _structured_from_answers({"primary_complaint": "x", "severity": 2})
        out = _fallback_summary(structured, "fr")
        assert "Интенсивность" in out  # ru fallback

    def test_fallback_summary_no_data_returns_locale_default(self):
        empty = _structured_from_answers({})
        out_ru = _fallback_summary(empty, "ru")
        out_en = _fallback_summary(empty, "en")
        assert out_ru and out_en
        assert out_ru != out_en  # different locale fallbacks

    def test_fallback_report_uses_gp_specialist(self):
        structured = _structured_from_answers({})
        session = _session(answers={}, red_flags=["from session"])
        report = _fallback_report(structured, session)
        assert report.specialist_recommendation.category == "gp"
        assert "from session" in report.detected_red_flags
