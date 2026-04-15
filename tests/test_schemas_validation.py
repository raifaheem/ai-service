import pytest
from pydantic import ValidationError

from app.schemas import ChatRequest, UserProfile


# --------------- conversation_id validation ---------------

class TestConversationIdValidation:
    def test_valid_uuid(self):
        req = ChatRequest(
            message="test",
            conversation_id="550e8400-e29b-41d4-a716-446655440000",
        )
        assert req.conversation_id == "550e8400-e29b-41d4-a716-446655440000"

    def test_none_is_allowed(self):
        req = ChatRequest(message="test")
        assert req.conversation_id is None

    def test_invalid_format_rejected(self):
        with pytest.raises(ValidationError):
            ChatRequest(message="test", conversation_id="not-a-uuid")

    def test_too_long_rejected(self):
        with pytest.raises(ValidationError):
            ChatRequest(message="test", conversation_id="a" * 100)

    def test_injection_in_id_rejected(self):
        with pytest.raises(ValidationError):
            ChatRequest(message="test", conversation_id="'; DROP TABLE users;--")


# --------------- profile list validation ---------------

class TestProfileListValidation:
    def test_valid_conditions(self):
        p = UserProfile(conditions=["diabetes", "hypertension"])
        assert len(p.conditions) == 2

    def test_too_many_conditions(self):
        with pytest.raises(ValidationError, match="maximum 20"):
            UserProfile(conditions=["condition"] * 21)

    def test_condition_too_long(self):
        with pytest.raises(ValidationError, match="maximum 200"):
            UserProfile(conditions=["x" * 201])

    def test_valid_goals(self):
        p = UserProfile(goals=["lose weight", "sleep better"])
        assert len(p.goals) == 2

    def test_too_many_goals(self):
        with pytest.raises(ValidationError, match="maximum 20"):
            UserProfile(goals=["goal"] * 21)

    def test_valid_allergies(self):
        p = UserProfile(allergies=["penicillin"])
        assert p.allergies == ["penicillin"]

    def test_too_many_allergies(self):
        with pytest.raises(ValidationError, match="maximum 20"):
            UserProfile(allergies=["allergy"] * 21)

    def test_valid_medications(self):
        p = UserProfile(medications=["metformin"])
        assert p.medications == ["metformin"]

    def test_too_many_medications(self):
        with pytest.raises(ValidationError, match="maximum 20"):
            UserProfile(medications=["med"] * 21)

    def test_none_lists_allowed(self):
        p = UserProfile()
        assert p.conditions is None
        assert p.goals is None
        assert p.allergies is None
        assert p.medications is None

    def test_empty_lists_allowed(self):
        p = UserProfile(conditions=[], goals=[])
        assert p.conditions == []
        assert p.goals == []


# --------------- metadata size validation ---------------

class TestMetadataValidation:
    def test_small_metadata_accepted(self):
        req = ChatRequest(message="test", metadata={"key": "value"})
        assert req.metadata == {"key": "value"}

    def test_none_metadata_accepted(self):
        req = ChatRequest(message="test")
        assert req.metadata is None

    def test_oversized_metadata_rejected(self):
        # Create metadata larger than 5KB
        big_meta = {"data": "x" * 6000}
        with pytest.raises(ValidationError, match="5120"):
            ChatRequest(message="test", metadata=big_meta)

    def test_just_under_limit_accepted(self):
        # ~4KB should be fine
        meta = {"data": "x" * 4000}
        req = ChatRequest(message="test", metadata=meta)
        assert req.metadata is not None
