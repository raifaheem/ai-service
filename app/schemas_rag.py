from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RAGChunkIn(BaseModel):
    text: str = Field(
        ...,
        min_length=1,
        max_length=8000,
        description="Chunk text content (max 8000 chars). The header-aware chunker keeps real chunks under ~1500 chars; the cap exists to bound dev-route abuse.",
    )
    source_id: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Identifier of the source document this chunk belongs to.",
    )
    title: str | None = Field(default=None, max_length=500, description="Optional source title.")
    language: str = Field(default="ru", max_length=8, description="Chunk language ('ru', 'en', 'kk').")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Free-form chunk metadata stored in Qdrant payload."
    )


class RAGIndexRequest(BaseModel):
    chunks: list[RAGChunkIn] = Field(
        ...,
        max_length=200,
        description="Chunks to upsert into the vector store (max 200 per request).",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "chunks": [
                        {
                            "text": "Tension-type headache is the most common primary headache...",
                            "source_id": "who-headache-2024",
                            "title": "Headache management — clinical guidelines",
                            "language": "en",
                            "metadata": {"chunk_index": 1, "section": "overview"},
                        }
                    ]
                }
            ]
        }
    )


class RAGSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="Natural-language search query (max 500 chars).")
    limit: int = Field(default=5, ge=1, le=20, description="Max number of chunks to return (1–20).")
    language: str | None = Field(
        default=None,
        max_length=8,
        description="Optional language filter. When omitted, all languages are searched.",
    )

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"query": "migraine triggers", "limit": 5, "language": "en"}]}
    )


class RAGSearchResult(BaseModel):
    id: str = Field(..., description="Qdrant point id.")
    score: float = Field(..., description="Cosine similarity score (0–1).")
    text: str = Field(..., description="Retrieved chunk text.")
    source_id: str = Field(..., description="Source document id.")
    title: str | None = Field(default=None, description="Source title.")
    language: str | None = Field(default=None, description="Chunk language.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Stored payload metadata.")


class RAGSearchResponse(BaseModel):
    query: str = Field(..., description="Echo of the input query.")
    results: list[RAGSearchResult] = Field(..., description="Matching chunks sorted by similarity.")


class RAGStatsResponse(BaseModel):
    collection: str = Field(..., description="Qdrant collection name backing the RAG corpus.")
    total_chunks: int = Field(..., description="Total number of chunks (points) stored in the collection.")
    total_sources: int = Field(..., description="Total number of distinct source_id values across the corpus.")
    by_language: dict[str, int] = Field(
        ...,
        description="Map of language code → number of chunks in that language (includes only languages actually present).",
    )
    sources_by_language: dict[str, int] = Field(
        ...,
        description="Map of language code → number of distinct source_id values in that language.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "collection": "medical_articles",
                    "total_chunks": 124,
                    "total_sources": 30,
                    "by_language": {"ru": 42, "en": 41, "kk": 41},
                    "sources_by_language": {"ru": 10, "en": 10, "kk": 10},
                }
            ]
        }
    )


class RAGDeleteSourceResponse(BaseModel):
    source_id: str = Field(..., description="The source_id that was targeted.")
    deleted: int = Field(..., description="Number of chunks (points) removed from the collection.")

    model_config = ConfigDict(json_schema_extra={"examples": [{"source_id": "who-headache-2024", "deleted": 6}]})
