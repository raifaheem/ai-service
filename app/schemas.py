from pydantic import BaseModel, Field
from typing import Optional, List, Literal, Dict, Any


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


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    locale: str = Field(default="ru")
    profile: Optional[UserProfile] = None
    conversation_id: Optional[str] = None
    history: Optional[List[HistoryTurn]] = None
    metadata: Optional[Dict[str, Any]] = None


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
