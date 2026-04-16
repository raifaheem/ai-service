from .vector_store import search_text_chunks


async def retrieve_context(
    query: str,
    limit: int = 5,
    language: str | None = None,
    redis_client=None,
    fallback_languages: list[str] | None = None,
) -> list[dict]:
    return await search_text_chunks(
        query=query,
        limit=limit,
        language=language,
        redis_client=redis_client,
        fallback_languages=fallback_languages,
    )


async def build_rag_context(
    query: str,
    limit: int = 5,
    language: str | None = None,
    redis_client=None,
    fallback_languages: list[str] | None = None,
) -> tuple[str, list[dict], float | None]:
    chunks = await retrieve_context(
        query=query,
        limit=limit,
        language=language,
        redis_client=redis_client,
        fallback_languages=fallback_languages if fallback_languages is not None else ["ru", "en"],
    )

    if not chunks:
        return "", [], None

    rag_score = sum(c["score"] for c in chunks) / len(chunks)

    parts: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        title = chunk.get("title") or "Untitled"
        source_id = chunk.get("source_id") or "unknown"
        text = chunk.get("text") or ""

        parts.append(f"[SOURCE {i}]\n" f"title: {title}\n" f"source_id: {source_id}\n" f"text: {text}")

    return "\n\n".join(parts), chunks, rag_score


def compress_sources(chunks: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen: set[tuple[str, str | None]] = set()

    for chunk in chunks:
        source_id = chunk.get("source_id", "")
        title = chunk.get("title")
        key = (source_id, title)
        if key in seen:
            continue
        seen.add(key)

        result.append(
            {
                "source_id": source_id,
                "title": title,
                "language": chunk.get("language"),
                "score": float(chunk.get("score", 0.0)),
            }
        )

    return result
