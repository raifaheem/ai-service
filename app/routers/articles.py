import logging
import uuid

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form

from ..schemas_articles import ArticleAnalyzeRequest, ArticleAnalysisResponse
from ..security import auth_guard
from ..services.article_analyzer import analyze_article_text
from ..services.article_parser import chunk_article_text
from ..services.vector_store import upsert_text_chunks
from ..services.file_text_extract import (
    extract_text_from_file,
    is_supported_article_file,
)

router = APIRouter(prefix="/v1/articles", tags=["articles"])


async def _run_article_pipeline(
    *,
    title: str,
    text: str,
    language: str,
    source_id: str | None,
    index_chunks: bool,
) -> ArticleAnalysisResponse:
    clean_text = text.strip()
    if len(clean_text) < 200:
        raise HTTPException(status_code=400, detail="Article text is too short")

    final_source_id = source_id or f"article-{uuid.uuid4()}"
    chunks = chunk_article_text(clean_text)

    indexed_chunks = 0
    if index_chunks:
        vector_chunks = [
            {
                "text": chunk,
                "source_id": final_source_id,
                "title": title,
                "language": language,
                "metadata": {
                    "type": "medical_article",
                    "chunk_index": idx,
                    "total_chunks": len(chunks),
                },
            }
            for idx, chunk in enumerate(chunks, start=1)
        ]
        indexed_chunks = await upsert_text_chunks(vector_chunks)

    try:
        analysis = await analyze_article_text(
            title=title,
            text=clean_text[:12000],
            language=language,
        )
    except Exception as e:
        logger.exception("Article analysis failed")
        raise HTTPException(status_code=502, detail="Article analysis failed")

    return ArticleAnalysisResponse(
        source_id=final_source_id,
        title=title,
        language=language,
        indexed_chunks=indexed_chunks,
        extracted_chars=len(clean_text),
        summary=analysis["summary"],
        key_findings=analysis["key_findings"],
        limitations=analysis["limitations"],
        practical_meaning=analysis["practical_meaning"],
        red_flags=analysis["red_flags"],
        confidence=analysis["confidence"],
    )


@router.post(
    "/analyze",
    response_model=ArticleAnalysisResponse,
    dependencies=[Depends(auth_guard)],
)
async def analyze_article(payload: ArticleAnalyzeRequest):
    return await _run_article_pipeline(
        title=payload.title,
        text=payload.text,
        language=payload.language,
        source_id=payload.source_id,
        index_chunks=payload.index_chunks,
    )


@router.post(
    "/analyze-file",
    response_model=ArticleAnalysisResponse,
    dependencies=[Depends(auth_guard)],
)
async def analyze_article_file(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    language: str = Form(default="ru"),
    source_id: str | None = Form(default=None),
    index_chunks: bool = Form(default=True),
):
    filename = file.filename or "uploaded_file"
    if not is_supported_article_file(filename):
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Use .txt, .pdf, or .docx",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    max_size = 10 * 1024 * 1024  # 10 MB
    if len(data) > max_size:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 10 MB")

    try:
        extracted_text = extract_text_from_file(filename, data)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to extract text from file: {e}",
        )

    final_title = (title or filename).strip()
    if not final_title:
        final_title = "Uploaded medical article"

    return await _run_article_pipeline(
        title=final_title,
        text=extracted_text,
        language=language,
        source_id=source_id,
        index_chunks=index_chunks,
    )