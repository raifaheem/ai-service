from pydantic import BaseModel, Field
from typing import Optional, List, Literal, Dict, Any


class UserProfile(BaseModel):
    age: Optional[int] = Field(default=None, ge=0, le=120)
    sex: Optional[Literal["male", "female", "other"]] = None
    conditions: Optional[List[str]] = None
    goals: Optional[List[str]] = None


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


class ChatResponse(BaseModel):
    answer: str
    disclaimer: str
    conversation_id: Optional[str] = None
    rag_used: bool = False
    sources: Optional[List[ChatSource]] = None
