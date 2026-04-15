import pytest

from app.schemas import ChatRequest, UserProfile
from app.routers.chat import profile_to_text


# --------------- profile_to_text ---------------

def test_profile_to_text_none():
    req = ChatRequest(message="test")
    assert profile_to_text(req) is None


def test_profile_to_text_basic_ru():
    req = ChatRequest(
        message="test",
        profile=UserProfile(age=30, sex="male"),
    )
    result = profile_to_text(req, locale="ru")
    assert "Возраст: 30" in result
    assert "Пол: male" in result


def test_profile_to_text_basic_en():
    req = ChatRequest(
        message="test",
        profile=UserProfile(age=25, sex="female"),
    )
    result = profile_to_text(req, locale="en")
    assert "Age: 25" in result
    assert "Sex: female" in result


def test_profile_to_text_basic_kk():
    req = ChatRequest(
        message="test",
        profile=UserProfile(age=40),
    )
    result = profile_to_text(req, locale="kk")
    assert "Жасы: 40" in result


def test_profile_to_text_with_new_fields():
    req = ChatRequest(
        message="test",
        profile=UserProfile(
            age=35,
            height_cm=175,
            weight_kg=80.0,
            activity_level="moderate",
            allergies=["penicillin"],
            medications=["metformin"],
        ),
    )
    result = profile_to_text(req, locale="en")
    assert "Height (cm): 175" in result
    assert "Weight (kg): 80.0" in result
    assert "BMI:" in result
    assert "Activity level: moderate" in result
    assert "Allergies: penicillin" in result
    assert "Current medications: metformin" in result


def test_profile_to_text_bmi_calculation():
    req = ChatRequest(
        message="test",
        profile=UserProfile(height_cm=180, weight_kg=80.0),
    )
    result = profile_to_text(req, locale="en")
    # BMI = 80 / (1.80^2) = 24.7
    assert "BMI: 24.7" in result


def test_profile_to_text_no_bmi_without_both():
    req = ChatRequest(
        message="test",
        profile=UserProfile(height_cm=180),
    )
    result = profile_to_text(req, locale="en")
    assert "BMI" not in result


def test_profile_to_text_empty_profile():
    req = ChatRequest(
        message="test",
        profile=UserProfile(),
    )
    assert profile_to_text(req) is None


def test_profile_to_text_conditions_and_goals():
    req = ChatRequest(
        message="test",
        profile=UserProfile(
            conditions=["diabetes", "hypertension"],
            goals=["lose weight", "improve sleep"],
        ),
    )
    result = profile_to_text(req, locale="ru")
    assert "Хронические/особенности: diabetes, hypertension" in result
    assert "Цели: lose weight, improve sleep" in result
