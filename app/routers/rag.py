from fastapi import APIRouter, Depends, HTTPException, Path

from ..schemas_rag import (
    RAGDeleteSourceResponse,
    RAGIndexRequest,
    RAGSearchRequest,
    RAGSearchResponse,
    RAGSearchResult,
    RAGStatsResponse,
)
from ..security import auth_guard
from ..services.rag import retrieve_context
from ..services.vector_store import (
    collect_corpus_stats,
    delete_chunks_by_source,
    upsert_text_chunks,
)

router = APIRouter(prefix="/v1/rag", tags=["dev-rag"])


@router.post(
    "/index",
    dependencies=[Depends(auth_guard)],
    summary="[Dev] Upsert arbitrary chunks into the vector store",
    description=(
        "**Dev-only** — available only when `ENABLE_DEV_ROUTES=true`. "
        "Inserts raw text chunks directly into Qdrant. Prefer `/v1/articles/analyze` in production "
        "so the article analyzer and chunker are applied consistently."
    ),
    responses={
        401: {"description": "Missing or invalid authentication."},
    },
)
async def index_rag_chunks(payload: RAGIndexRequest):
    chunks = [
        {
            "text": item.text,
            "source_id": item.source_id,
            "title": item.title,
            "language": item.language,
            "metadata": item.metadata,
        }
        for item in payload.chunks
    ]

    count = await upsert_text_chunks(chunks)
    return {"status": "ok", "indexed": count}


@router.post(
    "/search",
    response_model=RAGSearchResponse,
    dependencies=[Depends(auth_guard)],
    summary="[Dev] Semantic search over the RAG corpus",
    description=(
        "**Dev-only** — available only when `ENABLE_DEV_ROUTES=true`. "
        "Embeds the query and returns the top-matching chunks from Qdrant. "
        "Useful for debugging retrieval quality without running a full chat turn."
    ),
    responses={
        400: {"description": "Query is empty."},
        401: {"description": "Missing or invalid authentication."},
    },
)
async def search_rag(payload: RAGSearchRequest):
    if not payload.query.strip():
        raise HTTPException(status_code=400, detail="Query is empty")

    items = await retrieve_context(
        query=payload.query,
        limit=payload.limit,
        language=payload.language,
    )

    return RAGSearchResponse(
        query=payload.query,
        results=[RAGSearchResult(**item) for item in items],
    )


@router.get(
    "/stats",
    response_model=RAGStatsResponse,
    dependencies=[Depends(auth_guard)],
    summary="[Dev] RAG corpus statistics",
    description=(
        "**Dev-only** — available only when `ENABLE_DEV_ROUTES=true`. "
        "Returns total chunk count, total distinct sources, and per-language breakdowns. "
        "Used by `scripts/verify_knowledge_base.py` to assert minimum coverage "
        "(≥10 sources per locale) after seeding."
    ),
    responses={
        401: {"description": "Missing or invalid authentication."},
    },
)
async def rag_stats() -> RAGStatsResponse:
    stats = await collect_corpus_stats()
    return RAGStatsResponse(**stats)


@router.delete(
    "/source/{source_id}",
    response_model=RAGDeleteSourceResponse,
    dependencies=[Depends(auth_guard)],
    summary="[Dev] Delete every chunk belonging to a source_id",
    description=(
        "**Dev-only** — available only when `ENABLE_DEV_ROUTES=true`. "
        "Removes every point with `payload.source_id == source_id`. "
        "Called by `scripts/seed_knowledge_base.py` before re-inserting when "
        "`--overwrite` is set (the default), so seeding is idempotent."
    ),
    responses={
        401: {"description": "Missing or invalid authentication."},
    },
)
async def rag_delete_source(
    source_id: str = Path(..., min_length=1, max_length=200, description="Source identifier to purge."),
) -> RAGDeleteSourceResponse:
    deleted = await delete_chunks_by_source(source_id)
    return RAGDeleteSourceResponse(source_id=source_id, deleted=deleted)
