"""Tests for D.1 — region-aware emergency phone resolution.

Contract:
- get_emergency_phone(region, locale) resolves an ISO 3166-1 alpha-2 region
  code to a concrete phone string, or falls back to a locale-neutral default.
- _resolve_addon_prompt formats the emergency addon template with the resolved
  phone; non-emergency addons pass through unchanged.
- The raw ADDON_PROMPTS["emergency"] template carries the {emergency_phone}
  placeholder — guarded in tests/test_prompts.py::test_emergency_addon_uses_phone_placeholder.
"""

import pytest

from app.routers.chat import _resolve_addon_prompt
from app.services.i18n import EMERGENCY_PHONES, get_emergency_phone
from app.services.intent import IntentResult


def _emergency_intent() -> IntentResult:
    return IntentResult(
        category="emergency",
        confidence=0.95,
        requires_followup=False,
        detected_entities={},
        risk_level="emergency",
    )


def _symptom_intent() -> IntentResult:
    return IntentResult(
        category="symptom_check",
        confidence=0.9,
        requires_followup=False,
        detected_entities={},
        risk_level="medium",
    )


# --------------- get_emergency_phone ---------------


@pytest.mark.parametrize(
    "region,expected",
    [
        ("US", "911"),
        ("CA", "911"),
        ("GB", "999"),
        ("KZ", "112 / 103"),
        ("RU", "112 / 103"),
        ("DE", "112"),
        ("FR", "112"),
        ("AU", "000"),
        ("JP", "119"),
    ],
)
def test_known_region_returns_mapped_phone(region, expected):
    # Locale must not override a mapped region — the number itself is universal.
    for locale in ("ru", "en", "kk"):
        assert get_emergency_phone(region, locale) == expected


def test_region_is_case_insensitive():
    assert get_emergency_phone("kz", "ru") == "112 / 103"
    assert get_emergency_phone("Us", "en") == "911"
    assert get_emergency_phone("gb", "en") == "999"


def test_unknown_region_falls_back_to_locale_default():
    # Unknown ISO code → locale default.
    assert get_emergency_phone("ZZ", "ru") == "112 или 103"
    assert get_emergency_phone("XX", "kk") == "112 немесе 103"
    # English default is intentionally region-neutral to avoid the pre-D.1
    # bug of handing a KZ-based English caller "911".
    assert get_emergency_phone("XX", "en") == "your local emergency number"


def test_none_region_falls_back_to_locale_default():
    assert get_emergency_phone(None, "ru") == "112 или 103"
    assert get_emergency_phone(None, "en") == "your local emergency number"
    assert get_emergency_phone(None, "kk") == "112 немесе 103"


def test_empty_region_treated_as_missing():
    assert get_emergency_phone("", "ru") == "112 или 103"


def test_unknown_locale_falls_back_to_ru_default():
    # normalize_locale maps unknown locales to 'ru'.
    assert get_emergency_phone(None, "de") == "112 или 103"


def test_english_default_never_assumes_us():
    # Regression guard for the whole reason D.1 exists: no "911" leaks into
    # the en fallback path, because an English-speaking user may be anywhere.
    assert "911" not in get_emergency_phone(None, "en")
    assert "911" not in get_emergency_phone("ZZ", "en")


def test_map_has_no_duplicate_or_empty_values():
    # Sanity: every mapped value is a non-empty string; no accidental None.
    for region, phone in EMERGENCY_PHONES.items():
        assert isinstance(region, str) and len(region) == 2
        assert isinstance(phone, str) and phone.strip(), f"empty phone for {region}"


# --------------- _resolve_addon_prompt formats the emergency template ---------------


@pytest.mark.parametrize(
    "locale,region,expected_phone",
    [
        ("en", "US", "911"),
        ("en", "KZ", "112 / 103"),  # <-- the D.1 bug fix: English + KZ now correct.
        ("en", "GB", "999"),
        ("ru", "RU", "112 / 103"),
        ("ru", "US", "911"),
        ("kk", "KZ", "112 / 103"),
        ("kk", "DE", "112"),
    ],
)
def test_emergency_addon_contains_region_phone(locale, region, expected_phone):
    addon = _resolve_addon_prompt(_emergency_intent(), locale, region)
    assert addon is not None
    assert expected_phone in addon, (
        f"[{locale}/{region}] expected '{expected_phone}' in addon, got: {addon[:200]}"
    )
    # Placeholder must be resolved — the raw template token should never leak to the LLM.
    assert "{emergency_phone}" not in addon


def test_emergency_addon_without_region_uses_locale_default():
    # No region → locale-neutral English phrase, not "911".
    en_addon = _resolve_addon_prompt(_emergency_intent(), "en", None)
    assert en_addon is not None
    assert "your local emergency number" in en_addon
    assert "911" not in en_addon
    assert "{emergency_phone}" not in en_addon

    ru_addon = _resolve_addon_prompt(_emergency_intent(), "ru", None)
    assert ru_addon is not None
    assert "112 или 103" in ru_addon
    assert "{emergency_phone}" not in ru_addon


def test_non_emergency_addon_is_not_formatted():
    # symptom_check has no {emergency_phone} placeholder; formatting it
    # accidentally would crash with KeyError. Make sure we don't.
    addon = _resolve_addon_prompt(_symptom_intent(), "en", "KZ")
    assert addon is not None
    assert "SYMPTOM" in addon.upper() or "symptom" in addon.lower()
    assert "{emergency_phone}" not in addon  # placeholder never appears here anyway


def test_no_addon_when_category_has_none():
    # general_health maps to addon_name=None — region hint is a no-op.
    intent = IntentResult(
        category="general_health",
        confidence=0.9,
        requires_followup=False,
        detected_entities={},
        risk_level="low",
    )
    assert _resolve_addon_prompt(intent, "en", "US") is None
    assert _resolve_addon_prompt(intent, "en", None) is None
