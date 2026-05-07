"""Tests for the deterministic red-flag keyword detector (S3)."""

import pytest

from app.services.triage_redflags import keyword_red_flag


@pytest.mark.parametrize(
    "text, locale",
    [
        ("У меня сильная боль в груди", "ru"),
        ("Боль в груди отдаёт в левую руку", "ru"),
        ("Утром потерял сознание на минуту", "ru"),
        ("Не могу дышать, тяжело лежать", "ru"),
        ("Сильное кровотечение из носа уже час", "ru"),
        ("Думаю про суицид всё чаще", "ru"),
        ("Хочу умереть, не вижу смысла", "ru"),
    ],
)
def test_russian_red_flags_detected(text, locale):
    assert keyword_red_flag(text, locale) is not None


@pytest.mark.parametrize(
    "text",
    [
        "I have severe chest pain that radiates to my arm",
        "I lost consciousness for a few seconds",
        "Heavy bleeding from a wound for the last hour",
        "I'm thinking about suicide",
        "I want to kill myself",
        "Coughing up blood since morning",
        "Numbness in arm started today",
    ],
)
def test_english_red_flags_detected(text):
    assert keyword_red_flag(text, "en") is not None


@pytest.mark.parametrize(
    "text",
    [
        "Just a mild headache for a couple of days",
        "I feel a little tired",
        "Some back pain after gardening",
        "ate too much yesterday",
    ],
)
def test_benign_answers_pass(text):
    assert keyword_red_flag(text, "en") is None


@pytest.mark.parametrize(
    "text",
    [
        "Просто слегка болит голова",
        "Чувствую себя нормально",
        "Спал плохо, устал",
    ],
)
def test_benign_russian_passes(text):
    assert keyword_red_flag(text, "ru") is None


def test_unknown_locale_falls_back_to_ru():
    """Unknown locale should still detect Russian patterns (i18n fallback)."""
    assert keyword_red_flag("боль в груди", "fr") is not None


def test_empty_text_returns_none():
    assert keyword_red_flag("", "en") is None
    assert keyword_red_flag("   ", "en") is None
