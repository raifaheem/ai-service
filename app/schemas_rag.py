from pydantic import BaseModel, Field
from typing import Any


class RAGChunkIn(BaseModel):
    text: str = Field(..., min_length=1)
    source_id: str = Field(..., min_length=1)
    title: str | None = None
    language: str = "ru"
    metadata: dict[str, Any] = Field(default_factory=dict)


class RAGIndexRequest(BaseModel):
    chunks: list[RAGChunkIn]


class RAGSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(default=5, ge=1, le=20)
    language: str | None = None


class RAGSearchResult(BaseModel):
    id: str
    score: float
    text: str
    source_id: str
    title: str | None = None
    language: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RAGSearchResponse(BaseModel):
    query: str
    results: list[RAGSearchResult]