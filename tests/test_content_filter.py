import pytest

from app.services.content_filter import check_response_safety


# --------------- Dosage detection ---------------

class TestDosageFilter:
    def test_no_dosage_clean_response(self):
        text = "Rest well and stay hydrated. If symptoms persist, see a doctor."
        filtered, filters = check_response_safety(text, locale="en")
        assert filtered == text
        assert filters == []

    def test_detects_take_dosage_en(self):
        text = "You should take 500mg of ibuprofen twice daily."
        filtered, filters = check_response_safety(text, locale="en")
        assert "dosage_warning" in filters
        assert "consult your doctor" in filtered

    def test_detects_take_dosage_ru(self):
        text = "Принимайте 500 мг парацетамола каждые 6 часов."
        filtered, filters = check_response_safety(text, locale="ru")
        assert "dosage_warning" in filters
        assert "врач" in filtered

    def test_detects_dose_of_pattern(self):
        text = "The recommended dose of 10mg should be taken once."
        filtered, filters = check_response_safety(text, locale="en")
        assert "dosage_warning" in filters

    def test_detects_dosage_times_pattern(self):
        text = "250mg 3 times a day is the standard."
        filtered, filters = check_response_safety(text, locale="en")
        assert "dosage_warning" in filters

    def test_no_false_positive_on_measurement(self):
        # "500mg" alone without take/dose context should not trigger
        text = "Ibuprofen comes in 200mg and 400mg tablets."
        filtered, filters = check_response_safety(text, locale="en")
        assert "dosage_warning" not in filters

    def test_dosage_filter_applied_once(self):
        text = "Take 500mg paracetamol. Also take 200mg ibuprofen."
        filtered, filters = check_response_safety(text, locale="en")
        # Should have exactly one "dosage_warning" even with multiple matches
        assert filters.count("dosage_warning") == 1


# --------------- Diagnosis detection ---------------

class TestDiagnosisFilter:
    def test_no_diagnosis_clean(self):
        text = "These symptoms could indicate several conditions. Please consult a doctor."
        filtered, filters = check_response_safety(text, locale="en")
        assert "diagnosis_softened" not in filters

    def test_detects_you_have(self):
        text = "You have diabetes based on these symptoms."
        filtered, filters = check_response_safety(text, locale="en")
        assert "diagnosis_softened" in filters

    def test_detects_russian_diagnosis(self):
        text = "У вас гастрит, необходимо лечение."
        filtered, filters = check_response_safety(text, locale="ru")
        assert "diagnosis_softened" in filters

    def test_detects_definitely_this_is(self):
        text = "This is definitely pneumonia, start antibiotics."
        filtered, filters = check_response_safety(text, locale="en")
        assert "diagnosis_softened" in filters

    def test_no_false_positive_on_suggestion(self):
        text = "This may suggest a mild infection. Please see your doctor."
        filtered, filters = check_response_safety(text, locale="en")
        assert "diagnosis_softened" not in filters


# --------------- Locale handling ---------------

class TestLocaleHandling:
    def test_unknown_locale_falls_back_to_ru(self):
        text = "Принимайте 500 мг аспирина."
        filtered, filters = check_response_safety(text, locale="fr")
        assert "dosage_warning" in filters
        assert "врач" in filtered

    def test_kk_locale_dosage(self):
        text = "Қабылдаңыз 500 мг парацетамол."
        filtered, filters = check_response_safety(text, locale="kk")
        assert "dosage_warning" in filters
        assert "дәрігер" in filtered


# --------------- Combined filters ---------------

class TestCombinedFilters:
    def test_both_dosage_and_diagnosis(self):
        text = "You have bronchitis. Take 500mg amoxicillin 3 times a day."
        filtered, filters = check_response_safety(text, locale="en")
        assert "dosage_warning" in filters
        assert "diagnosis_softened" in filters

    def test_empty_response(self):
        filtered, filters = check_response_safety("", locale="en")
        assert filtered == ""
        assert filters == []
