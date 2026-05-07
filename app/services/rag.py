"""Thin RAG wrapper over `vector_store.search_text_chunks`.

`retrieve_context` and `build_rag_context` are the chat-pipeline entry points;
the latter assembles the formatted system-prompt block plus the per-chunk
metadata that ends up in `ChatResponse.sources`. RAG fails open at this layer:
if the underlying vector search raises (Qdrant down, breaker open), the chat
router catches the exception and proceeds without context.
"""

from .vector_store import search_text_chunks


async def retrieve_context(
    query: str,
    limit: int = 5,
    language: str | None = None,
    redis_client=None,
    fallback_languages: list[str] | None = None,
) -> list[dict]:
    """Return top-K matching chunks, optionally falling back across languages."""
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
    """Run RAG retrieval and return `(formatted_system_block, raw_chunks, mean_score)`.

    The formatted block is the `[SOURCE i] title / source_id / text` shape that
    the LLM sees; raw chunks are returned alongside so the router can compress
    them into `ChatResponse.sources`. Empty result returns `("", [], None)`.
    """
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
    """De-duplicate chunks by `(source_id, title)` and project to the API
    `ChatSource` shape. Surfaces `is_fallback` only when truthy."""
    result: list[dict] = []
    seen: set[tuple[str, str | None]] = set()

    for chunk in chunks:
        source_id = chunk.get("source_id", "")
        title = chunk.get("title")
        key = (source_id, title)
        if key in seen:
            continue
        seen.add(key)

        item: dict = {
            "source_id": source_id,
            "title": title,
            "language": chunk.get("language"),
            "score": float(chunk.get("score", 0.0)),
        }
        # M12: surface the cross-language-fallback flag so clients can mark
        # such hits visually. Only set when truthy — keeps the JSON tidy for
        # the common in-language case.
        if chunk.get("is_fallback"):
            item["is_fallback"] = True
        result.append(item)

    return result
