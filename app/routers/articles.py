import logging
import uuid

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile

from ..context import get_request_id
from ..schemas_articles import ArticleAnalysisResponse, ArticleAnalyzeRequest
from ..security import auth_guard, resolve_user_id
from ..services.article_analyzer import analyze_article_text
from ..services.article_parser import chunk_article_with_headers
from ..services.audit import EVENT_ARTICLE_SENSITIVE_BLOCKED, record_audit_event
from ..services.content_safety import SENSITIVE_REFUSAL, detect_sensitive_topic
from ..services.file_text_extract import (
    extract_text_from_file,
    is_supported_article_file,
)
from ..services.i18n import normalize_locale
from ..services.rate_limit import enforce_rate_limit
from ..services.vector_store import upsert_text_chunks

router = APIRouter(prefix="/v1/articles", tags=["articles"])


async def _run_article_pipeline(
    *,
    title: str,
    text: str,
    language: str,
    source_id: str | None,
    index_chunks: bool,
    user_id: str,
) -> ArticleAnalysisResponse:
    clean_text = text.strip()
    if len(clean_text) < 200:
        raise HTTPException(status_code=400, detail="Article text is too short")

    # Sensitive-topic policy: refuse to analyze or index off-policy content
    # (sex / profanity / recreational drugs / gore). Articles are admin-uploaded
    # but still must not poison the RAG corpus. Title and body are scanned
    # separately so the audit pinpoints which field tripped the gate.
    for field_name, field_value in (("title", title), ("text", clean_text)):
        sensitive = detect_sensitive_topic(field_value)
        if sensitive is not None:
            await record_audit_event(
                EVENT_ARTICLE_SENSITIVE_BLOCKED,
                user_id=user_id,
                request_id=get_request_id(),
                sensitive_category=sensitive.category,
                locale_hit=sensitive.locale_hit,
                pattern_id=sensitive.pattern_id,
                field=field_name,
                language=language,
            )
            refusal_locale = normalize_locale(language)
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "sensitive_content_blocked",
                    "category": sensitive.category,
                    "field": field_name,
                    "message": SENSITIVE_REFUSAL.get(refusal_locale, SENSITIVE_REFUSAL["ru"]),
                },
            )

    final_source_id = source_id or f"article-{uuid.uuid4()}"
    chunk_dicts = chunk_article_with_headers(clean_text)

    indexed_chunks = 0
    if index_chunks:
        vector_chunks = [
            {
                "text": chunk["text"],
                "source_id": final_source_id,
                "title": title,
                "language": language,
                "metadata": {
                    "type": "medical_article",
                    "chunk_index": idx,
                    "total_chunks": len(chunk_dicts),
                    "header": chunk.get("header"),
                },
            }
            for idx, chunk in enumerate(chunk_dicts, start=1)
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
        raise HTTPException(status_code=502, detail="Article analysis failed") from e

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
    summary="Analyze and optionally index a medical article (JSON body)",
    description=(
        "Chunks the article, optionally upserts the chunks into the Qdrant RAG corpus, "
        "and runs an LLM analysis that extracts summary, key findings, limitations, "
        "practical meaning, and red flags. When `index_chunks` is false, the article is "
        "only analyzed and is not made available to future RAG queries."
    ),
    responses={
        400: {"description": "Article text is too short (min 200 characters)."},
        401: {"description": "Missing or invalid authentication."},
        422: {"description": "Article content rejected on policy grounds (sensitive topic)."},
        429: {"description": "Rate limit exceeded."},
        502: {"description": "Upstream LLM analysis failed."},
    },
)
async def analyze_article(
    payload: ArticleAnalyzeRequest,
    auth=Depends(auth_guard),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    user_id = resolve_user_id(auth, x_user_id)
    await enforce_rate_limit(f"user:{user_id}")
    return await _run_article_pipeline(
        title=payload.title,
        text=payload.text,
        language=payload.language,
        source_id=payload.source_id,
        index_chunks=payload.index_chunks,
        user_id=user_id,
    )


@router.post(
    "/analyze-file",
    response_model=ArticleAnalysisResponse,
    summary="Analyze and optionally index a medical article (multipart file upload)",
    description=(
        "Accepts `.txt`, `.pdf`, or `.docx` (max 10 MB), extracts text, then runs the same "
        "pipeline as `/analyze`. Title defaults to the filename when not provided."
    ),
    responses={
        400: {"description": "Unsupported file type, empty file, or extraction failure."},
        401: {"description": "Missing or invalid authentication."},
        413: {"description": "File exceeds the 10 MB upload limit."},
        422: {"description": "Article content rejected on policy grounds (sensitive topic)."},
        429: {"description": "Rate limit exceeded."},
        502: {"description": "Upstream LLM analysis failed."},
    },
)
async def analyze_article_file(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    language: str = Form(default="ru"),
    source_id: str | None = Form(default=None),
    index_chunks: bool = Form(default=True),
    auth=Depends(auth_guard),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    user_id = resolve_user_id(auth, x_user_id)
    await enforce_rate_limit(f"user:{user_id}")
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
        ) from e

    final_title = (title or filename).strip()
    if not final_title:
        final_title = "Uploaded medical article"

    return await _run_article_pipeline(
        title=final_title,
        text=extracted_text,
        language=language,
        source_id=source_id,
        index_chunks=index_chunks,
        user_id=user_id,
    )
