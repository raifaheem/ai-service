from .vector_store import search_text_chunks


async def retrieve_context(
        query: str,
        limit: int = 5,
        language: str | None = None,
) -> list[dict]:
    return await search_text_chunks(
        query=query,
        limit=limit,
        language=language,
    )


async def build_rag_context(
        query: str,
        limit: int = 5,
        language: str | None = None,
) -> tuple[str, list[dict]]:
    chunks = await retrieve_context(
        query=query,
        limit=limit,
        language=language,
    )

    if not chunks:
        return "", []

    parts: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        title = chunk.get("title") or "Untitled"
        source_id = chunk.get("source_id") or "unknown"
        text = chunk.get("text") or ""

        parts.append(
            f"[SOURCE {i}]\n"
            f"title: {title}\n"
            f"source_id: {source_id}\n"
            f"text: {text}"
        )

    return "\n\n".join(parts), chunks


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
