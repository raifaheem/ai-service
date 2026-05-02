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


@pytest.mark.parametrize("locale", LOCALES)
def test_emergency_addon_uses_phone_placeholder(locale):
    """The emergency addon must carry a {emergency_phone} placeholder,
    not a hardcoded country-specific number. The placeholder is resolved
    per-request by chat.py::_resolve_addon_prompt using the client-supplied
    region hint (see D.1 in IMPROVEMENT_PLAN.md)."""
    text = ADDON_PROMPTS["emergency"][locale]
    assert "{emergency_phone}" in text, (
        f"emergency/{locale} must carry a {{emergency_phone}} placeholder so the "
        f"number can be injected per region. Got: {text[:200]}"
    )
    # Guard against regression: no hardcoded regional number in the raw template.
    for hardcoded in ("911", "999", "000"):
        assert hardcoded not in text, (
            f"emergency/{locale} contains hardcoded '{hardcoded}' — should use {{emergency_phone}} instead"
        )


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


# --------------- Disclaimer single-source-of-truth ---------------
# DISCLAIMERS[locale] is appended deterministically by chat.py / chat_stream.py.
# The system prompt must NOT instruct the model to also write its own disclaimer,
# otherwise both end up in the answer (regression seen in the field, May 2026).

_NO_DISCLAIMER_STEP_TOKENS = {
    "ru": ("Заверши дисклеймером", "носят информационный характер"),
    "en": ("End with a disclaimer", "answers are informational"),
    "kk": ("Дисклеймермен аяқта", "ақпараттық сипатта екенін"),
}


@pytest.mark.parametrize("locale", LOCALES)
def test_system_prompts_have_no_disclaimer_step(locale):
    prompt = SYSTEM_PROMPTS[locale]
    for token in _NO_DISCLAIMER_STEP_TOKENS[locale]:
        assert token not in prompt, (
            f"System prompt for '{locale}' still tells the model to write a disclaimer "
            f"({token!r}). The deterministic post-process in chat.py is the single source — "
            f"keeping the instruction here causes a duplicate disclaimer in every answer."
        )


# --------------- symptom_check addon must be history-aware ---------------
# The addon used to unconditionally tell the model to gather 4-5 clarifying details,
# which made the bot re-ask the same questions on every turn even after the user
# already answered. The fix instructs the model to consult history first.

_HISTORY_AWARE_TOKENS = {
    "ru": ("уже сообщил", "НЕ задавай"),
    "en": ("already", "DO NOT ask"),
    "kk": ("бұрын", "ҚАЙТА СҰРАМА"),
}


@pytest.mark.parametrize("locale", LOCALES)
def test_symptom_check_addon_is_history_aware(locale):
    # Case-insensitive — the prompt uses stylistic uppercase emphasis (УЖЕ, БҰРЫН, DO NOT)
    # that we don't want to pin in the test.
    addon = ADDON_PROMPTS["symptom_check"][locale].lower()
    for token in _HISTORY_AWARE_TOKENS[locale]:
        assert token.lower() in addon, (
            f"symptom_check addon for '{locale}' is missing history-awareness marker "
            f"{token!r}. Without it the model re-asks already-answered questions every turn."
        )
