import json
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal, Dict, Any

_MAX_LIST_ITEMS = 20
_MAX_ITEM_LENGTH = 200


def _validate_string_list(v: Optional[List[str]], field_name: str) -> Optional[List[str]]:
    if v is None:
        return v
    if len(v) > _MAX_LIST_ITEMS:
        raise ValueError(f"{field_name}: maximum {_MAX_LIST_ITEMS} items allowed")
    for i, item in enumerate(v):
        if len(item) > _MAX_ITEM_LENGTH:
            raise ValueError(f"{field_name}[{i}]: maximum {_MAX_ITEM_LENGTH} characters per item")
    return v


class UserProfile(BaseModel):
    age: Optional[int] = Field(default=None, ge=0, le=120)
    sex: Optional[Literal["male", "female", "other"]] = None
    conditions: Optional[List[str]] = None
    goals: Optional[List[str]] = None
    allergies: Optional[List[str]] = None
    medications: Optional[List[str]] = None
    height_cm: Optional[int] = Field(default=None, ge=50, le=300)
    weight_kg: Optional[float] = Field(default=None, ge=1, le=500)
    activity_level: Optional[Literal["sedentary", "light", "moderate", "active"]] = None

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
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=4000)


_UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
_MAX_METADATA_BYTES = 5120  # 5KB


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    locale: str = Field(default="ru")
    profile: Optional[UserProfile] = None
    conversation_id: Optional[str] = Field(default=None, max_length=36, pattern=_UUID_PATTERN)
    history: Optional[List[HistoryTurn]] = None
    metadata: Optional[Dict[str, Any]] = None

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
    source_id: str
    title: Optional[str] = None
    language: Optional[str] = None
    score: float


class ChatIntent(BaseModel):
    category: str
    risk_level: str
    confidence: float


class ChatResponse(BaseModel):
    answer: str
    disclaimer: str
    conversation_id: Optional[str] = None
    rag_used: bool = False
    rag_score: Optional[float] = None
    sources: Optional[List[ChatSource]] = None
    intent: Optional[ChatIntent] = None
