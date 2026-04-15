from fastapi import APIRouter, Depends, HTTPException

from ..schemas_rag import (
    RAGIndexRequest,
    RAGSearchRequest,
    RAGSearchResponse,
    RAGSearchResult,
)
from ..security import auth_guard
from ..services.rag import retrieve_context
from ..services.vector_store import upsert_text_chunks

router = APIRouter(prefix="/v1/rag", tags=["dev-rag"])


@router.post("/index", dependencies=[Depends(auth_guard)])
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