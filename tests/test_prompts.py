import pytest

from app.prompts import SYSTEM_PROMPTS, ADDON_PROMPTS, DISCLAIMERS
from app.services.i18n import get_prompt_addon


LOCALES = ["ru", "en", "kk"]
ADDON_NAMES = ["symptom_check", "lifestyle", "mental_health", "emergency"]


# --------------- SYSTEM_PROMPTS ---------------

@pytest.mark.parametrize("locale", LOCALES)
def test_system_prompts_all_locales_exist(locale):
    assert locale in SYSTEM_PROMPTS
    assert len(SYSTEM_PROMPTS[locale]) > 0


@pytest.mark.parametrize("locale", LOCALES)
def test_system_prompts_minimum_length(locale):
    word_count = len(SYSTEM_PROMPTS[locale].split())
    assert word_count >= 200, (
        f"System prompt for '{locale}' has only {word_count} words, expected >= 200"
    )


def test_system_prompts_safety_phrases_ru():
    prompt = SYSTEM_PROMPTS["ru"].lower()
    assert "диагноз" in prompt
    assert "скорую" in prompt or "112" in prompt or "103" in prompt
    assert "детей до 3" in prompt or "до 3 лет" in prompt
    assert "суицид" in prompt


def test_system_prompts_safety_phrases_en():
    prompt = SYSTEM_PROMPTS["en"].lower()
    assert "diagnosis" in prompt
    assert "emergency" in prompt or "911" in prompt
    assert "children under 3" in prompt
    assert "suicid" in prompt


def test_system_prompts_safety_phrases_kk():
    prompt = SYSTEM_PROMPTS["kk"].lower()
    assert "диагноз" in prompt
    assert "112" in prompt or "103" in prompt
    assert "3 жас" in prompt or "до 3" in prompt
    assert "суицид" in prompt


# --------------- ADDON_PROMPTS ---------------

@pytest.mark.parametrize("addon", ADDON_NAMES)
@pytest.mark.parametrize("locale", LOCALES)
def test_addon_prompts_all_locales_exist(addon, locale):
    assert addon in ADDON_PROMPTS
    assert locale in ADDON_PROMPTS[addon]
    assert len(ADDON_PROMPTS[addon][locale]) > 0


@pytest.mark.parametrize("addon", ADDON_NAMES)
@pytest.mark.parametrize("locale", LOCALES)
def test_addon_prompts_minimum_length(addon, locale):
    word_count = len(ADDON_PROMPTS[addon][locale].split())
    assert word_count >= 30, (
        f"Addon '{addon}' for '{locale}' has only {word_count} words, expected >= 30"
    )


def test_emergency_addon_contains_hotline_ru():
    text = ADDON_PROMPTS["emergency"]["ru"]
    assert "112" in text or "103" in text


def test_emergency_addon_contains_hotline_en():
    text = ADDON_PROMPTS["emergency"]["en"]
    assert "911" in text


def test_emergency_addon_contains_hotline_kk():
    text = ADDON_PROMPTS["emergency"]["kk"]
    assert "112" in text or "103" in text


# --------------- get_prompt_addon ---------------

def test_get_prompt_addon_returns_correct_locale():
    en_addon = get_prompt_addon("symptom_check", "en")
    assert en_addon is not None
    assert en_addon == ADDON_PROMPTS["symptom_check"]["en"]


def test_get_prompt_addon_unknown_returns_none():
    assert get_prompt_addon("nonexistent", "ru") is None


def test_get_prompt_addon_fallback_to_ru():
    result = get_prompt_addon("symptom_check", "de")
    assert result == ADDON_PROMPTS["symptom_check"]["ru"]


# --------------- DISCLAIMERS regression guard ---------------

def test_disclaimers_unchanged():
    assert DISCLAIMERS["ru"] == "Это не медицинский диагноз и не замена консультации врача."
    assert DISCLAIMERS["en"] == "This is not a medical diagnosis and does not replace consultation with a doctor."
    assert DISCLAIMERS["kk"] == "Бұл медициналық диагноз емес және дәрігер кеңесін алмастырмайды."
