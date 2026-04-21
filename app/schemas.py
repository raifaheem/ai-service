import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_MAX_LIST_ITEMS = 20
_MAX_ITEM_LENGTH = 200


def _validate_string_list(v: list[str] | None, field_name: str) -> list[str] | None:
    if v is None:
        return v
    if len(v) > _MAX_LIST_ITEMS:
        raise ValueError(f"{field_name}: maximum {_MAX_LIST_ITEMS} items allowed")
    for i, item in enumerate(v):
        if len(item) > _MAX_ITEM_LENGTH:
            raise ValueError(f"{field_name}[{i}]: maximum {_MAX_ITEM_LENGTH} characters per item")
    return v


class UserProfile(BaseModel):
    age: int | None = Field(default=None, ge=0, le=120, description="User age in years (0–120).")
    sex: Literal["male", "female", "other"] | None = Field(
        default=None, description="Biological sex. Used to tailor recommendations."
    )
    conditions: list[str] | None = Field(
        default=None,
        description="Chronic or current medical conditions (max 20 items, 200 chars each).",
    )
    goals: list[str] | None = Field(
        default=None,
        description="Health goals, e.g. 'lose weight', 'immunity', 'better sleep'.",
    )
    allergies: list[str] | None = Field(default=None, description="Known allergies (drug, food, environmental).")
    medications: list[str] | None = Field(default=None, description="Medications currently taken by the user.")
    height_cm: int | None = Field(default=None, ge=50, le=300, description="Height in centimeters.")
    weight_kg: float | None = Field(default=None, ge=1, le=500, description="Weight in kilograms.")
    activity_level: Literal["sedentary", "light", "moderate", "active"] | None = Field(
        default=None, description="Self-reported physical activity level."
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "age": 30,
                    "sex": "female",
                    "conditions": ["migraine"],
                    "goals": ["immunity", "better sleep"],
                    "allergies": ["pollen"],
                    "medications": [],
                    "height_cm": 168,
                    "weight_kg": 62.5,
                    "activity_level": "moderate",
                }
            ]
        }
    )

    @field_validator("conditions")
    @classmethod
    def validate_conditions(cls, v):
        return _validate_string_list(v, "conditions")

    @field_validator("goals")
    @classmethod
    def validate_goals(cls, v):
        return _validate_string_list(v, "goals")

    @field_validator("allergies")
    @classmethod
    def validate_allergies(cls, v):
        return _validate_string_list(v, "allergies")

    @field_validator("medications")
    @classmethod
    def validate_medications(cls, v):
        return _validate_string_list(v, "medications")


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"] = Field(..., description="Turn author. Either 'user' or 'assistant'.")
    content: str = Field(..., min_length=1, max_length=4000, description="Turn text content (1–4000 chars).")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"role": "user", "content": "У меня болит голова уже 3 дня"},
                {"role": "assistant", "content": "Понимаю. Уточните, где именно болит?"},
            ]
        }
    )


_UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
_MAX_METADATA_BYTES = 5120  # 5KB


class ChatRequest(BaseModel):
    message: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="User message to the assistant (1–4000 characters).",
    )
    locale: str = Field(
        default="ru",
        description="Response language. Supported: 'ru', 'en', 'kk'. Unknown values fall back to 'ru'.",
    )
    profile: UserProfile | None = Field(
        default=None,
        description="Optional user profile. When present, is serialized into the system prompt for personalization.",
    )
    conversation_id: str | None = Field(
        default=None,
        max_length=36,
        pattern=_UUID_PATTERN,
        description=(
            "Client-generated UUIDv4. When omitted, the server creates one and returns it in the response. "
            "Ownership is locked to the first user who writes to it."
        ),
    )
    history: list[HistoryTurn] | None = Field(
        default=None,
        description=(
            "Optional prior turns. When provided, replaces the server-stored history for this request "
            "(trimmed to the last 8). When omitted, history is loaded from Redis by `conversation_id`."
        ),
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Free-form client metadata. Max 5 KB when serialized to JSON.",
    )
    idempotency_key: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Optional client-supplied idempotency key, scoped per user. If a previous request with the "
            "same (user_id, idempotency_key) was answered in the last 10 minutes, the cached response is "
            "returned instead of running the pipeline again. Applies only to POST /v1/chat (not the SSE "
            "streaming endpoint). Use a fresh UUID per logical user action."
        ),
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "message": "У меня болит голова уже 3 дня, что делать?",
                    "locale": "ru",
                    "profile": {"age": 30, "sex": "female", "goals": ["immunity"]},
                },
                {
                    "message": "Какие витамины пить весной?",
                    "locale": "ru",
                    "conversation_id": "c3a1b2d4-5678-4abc-9def-0123456789ab",
                    "profile": {"age": 28, "sex": "female", "activity_level": "light"},
                    "metadata": {"client": "ios", "app_version": "1.2.3"},
                },
            ]
        }
    )

    @field_validator("metadata")
    @classmethod
    def validate_metadata_size(cls, v):
        if v is None:
            return v
        serialized = json.dumps(v, ensure_ascii=False)
        if len(serialized.encode("utf-8")) > _MAX_METADATA_BYTES:
            raise ValueError(f"metadata must not exceed {_MAX_METADATA_BYTES} bytes")
        return v


class ChatSource(BaseModel):
    source_id: str = Field(..., description="Identifier of the source document in the RAG corpus.")
    title: str | None = Field(default=None, description="Human-readable title of the source document.")
    language: str | None = Field(default=None, description="Language of the source chunk (ISO 639-1).")
    score: float = Field(..., description="Cosine similarity score of the retrieved chunk (0–1).")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "source_id": "article-headache-guide-2024",
                    "title": "Headache management — clinical guidelines",
                    "language": "ru",
                    "score": 0.82,
                }
            ]
        }
    )


class ChatIntent(BaseModel):
    category: str = Field(
        ..., description="Intent label (e.g. 'symptom_check', 'lifestyle', 'mental_health', 'emergency', 'off_topic')."
    )
    risk_level: str = Field(..., description="Risk tier ('low', 'medium', 'high') used to shape the answer.")
    confidence: float = Field(..., description="Classifier confidence in [0, 1].")

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"category": "symptom_check", "risk_level": "medium", "confidence": 0.88}]}
    )


class ChatResponse(BaseModel):
    answer: str = Field(
        ...,
        description=(
            "The assistant's full reply. Already content-safety filtered and has the locale-specific "
            "disclaimer appended if not already present."
        ),
    )
    disclaimer: str = Field(..., description="Standard medical disclaimer for the requested locale.")
    conversation_id: str | None = Field(
        default=None, description="The conversation UUID — echoed from the request or generated by the server."
    )
    rag_used: bool = Field(
        default=False, description="True when at least one retrieved chunk passed the RAG score threshold."
    )
    rag_score: float | None = Field(
        default=None, description="Top RAG chunk cosine similarity, or null when RAG was not used."
    )
    sources: list[ChatSource] | None = Field(
        default=None, description="Retrieved RAG sources used to ground the answer."
    )
    intent: ChatIntent | None = Field(
        default=None, description="Classified user intent (category / risk_level / confidence)."
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "answer": (
                        "Головная боль в течение 3 дней может быть связана с несколькими причинами… "
                        "Рекомендую проконсультироваться с врачом."
                    ),
                    "disclaimer": "Эта информация носит справочный характер и не заменяет консультацию врача.",
                    "conversation_id": "c3a1b2d4-5678-4abc-9def-0123456789ab",
                    "rag_used": True,
                    "rag_score": 0.82,
                    "sources": [
                        {
                            "source_id": "article-headache-guide-2024",
                            "title": "Headache management — clinical guidelines",
                            "language": "ru",
                            "score": 0.82,
                        }
                    ],
                    "intent": {"category": "symptom_check", "risk_level": "medium", "confidence": 0.88},
                }
            ]
        }
    )
