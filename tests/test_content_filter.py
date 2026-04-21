"""Content-filter tests (A.3): dosage detection appends a single footer.

The filter no longer rewrites the response body — only a locale-aware footer is
appended when any dosage pattern matches. The prior diagnosis-softening path has
been removed (that concern is enforced by the system prompt).
"""

from app.services.content_filter import _DOSAGE_FOOTER, check_response_safety


class TestDosageDetection:
    def test_no_dosage_leaves_response_unchanged(self):
        text = "Rest well and stay hydrated. If symptoms persist, see a doctor."
        filtered, filters = check_response_safety(text, locale="en")
        assert filtered == text
        assert filters == []

    def test_empty_response(self):
        filtered, filters = check_response_safety("", locale="en")
        assert filtered == ""
        assert filters == []

    def test_take_pattern_en(self):
        text = "You should take 500mg of ibuprofen twice daily."
        filtered, filters = check_response_safety(text, locale="en")
        assert filters == ["dosage_warning"]
        # Body is not modified in place.
        assert text in filtered
        # Footer appended exactly once at the end.
        assert filtered.endswith(_DOSAGE_FOOTER["en"])
        assert filtered.count(_DOSAGE_FOOTER["en"]) == 1

    def test_take_pattern_ru(self):
        text = "Принимайте 500 мг парацетамола каждые 6 часов."
        filtered, filters = check_response_safety(text, locale="ru")
        assert filters == ["dosage_warning"]
        assert filtered.endswith(_DOSAGE_FOOTER["ru"])

    def test_take_pattern_kk(self):
        text = "Қабылдаңыз 500 мг парацетамол."
        filtered, filters = check_response_safety(text, locale="kk")
        assert filters == ["dosage_warning"]
        assert filtered.endswith(_DOSAGE_FOOTER["kk"])

    def test_dose_of_pattern(self):
        text = "The recommended dose of 10mg should be taken once."
        filtered, filters = check_response_safety(text, locale="en")
        assert filters == ["dosage_warning"]
        assert filtered.endswith(_DOSAGE_FOOTER["en"])

    def test_times_per_day_pattern(self):
        text = "250mg 3 times a day is the standard."
        filtered, filters = check_response_safety(text, locale="en")
        assert filters == ["dosage_warning"]


class TestFooterAppendedOnce:
    def test_multiple_matches_still_single_footer(self):
        """One footer per response, regardless of how many dosage phrases match."""
        text = "Take 500 mg paracetamol, then take 200 mg ibuprofen. Dose of 100 mg otherwise."
        filtered, filters = check_response_safety(text, locale="en")
        assert filters == ["dosage_warning"]
        assert filtered.count(_DOSAGE_FOOTER["en"]) == 1

    def test_response_body_not_modified_inline(self):
        """No parenthetical insertions inside sentences — body stays verbatim."""
        text = "Принимайте 500 мг ибупрофена, а затем 200 мг парацетамола."
        filtered, filters = check_response_safety(text, locale="ru")
        body, footer_part = filtered.split("\n\n", 1)
        # Body is the original text (possibly right-stripped).
        assert body == text.rstrip()
        assert footer_part == _DOSAGE_FOOTER["ru"]
        # No parenthetical insertion slipped in mid-sentence.
        assert "(уточните" not in body
        assert "(consult" not in body

    def test_trailing_whitespace_collapsed(self):
        text = "Take 500mg paracetamol.   \n\n  "
        filtered, _ = check_response_safety(text, locale="en")
        # Only one blank line between body and footer.
        assert "\n\n\n" not in filtered


class TestNoFalsePositives:
    def test_product_listing_does_not_trigger(self):
        """'Ibuprofen comes in 200mg and 400mg tablets' is not a recommendation."""
        text = "Ibuprofen comes in 200mg and 400mg tablets."
        filtered, filters = check_response_safety(text, locale="en")
        assert filters == []
        assert filtered == text

    def test_no_inline_insertions_when_no_dosage(self):
        text = "Rest, drink water, and see a doctor if it worsens."
        filtered, filters = check_response_safety(text, locale="en")
        assert filters == []
        assert filtered == text


class TestLocaleFallback:
    def test_unknown_locale_falls_back_to_ru_footer(self):
        text = "Принимайте 500 мг аспирина."
        filtered, filters = check_response_safety(text, locale="fr")
        assert filters == ["dosage_warning"]
        assert filtered.endswith(_DOSAGE_FOOTER["ru"])


class TestDiagnosisSoftenerRemoved:
    """The diagnosis-softening filter was removed — system prompt handles that rule.

    These tests ensure no 'diagnosis_softened' flag ever fires and the response
    body is not rewritten for diagnosis-like phrasing.
    """

    def test_english_definitive_diagnosis_passes_through(self):
        text = "You have diabetes based on these symptoms."
        filtered, filters = check_response_safety(text, locale="en")
        assert filters == []
        assert filtered == text

    def test_russian_definitive_diagnosis_passes_through(self):
        text = "У вас гастрит, необходимо лечение."
        filtered, filters = check_response_safety(text, locale="ru")
        assert filters == []
        assert filtered == text

    def test_no_softener_insertions_anywhere(self):
        text = "This is definitely pneumonia, start antibiotics."
        filtered, filters = check_response_safety(text, locale="en")
        assert "This may suggest" not in filtered
        assert "Возможно" not in filtered
        assert filters == []
