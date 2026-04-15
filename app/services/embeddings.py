from openai import AsyncOpenAI
from ..config import settings

client = AsyncOpenAI(api_key=settings.openai_api_key)

EMBEDDING_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


def get_embedding_dimension() -> int:
    model = settings.openai_embedding_model
    size = EMBEDDING_DIMENSIONS.get(model)
    if size is None:
        raise RuntimeError(f"Unsupported embedding model: {model}")
    return size


def normalize_text_for_embedding(text: str) -> str:
    return " ".join(text.split()).strip()


async def embed_text(text: str) -> list[float]:
    normalized = normalize_text_for_embedding(text)
    if not normalized:
        raise ValueError("Text for embedding is empty")

    resp = await client.embeddings.create(
        model=settings.openai_embedding_model,
        input=normalized,
    )
    return resp.data[0].embedding


async def embed_texts(texts: list[str]) -> list[list[float]]:
    normalized = [normalize_text_for_embedding(t) for t in texts]
    normalized = [t for t in normalized if t]

    if not normalized:
        return []

    resp = await client.embeddings.create(
        model=settings.openai_embedding_model,
        input=normalized,
    )
    return [item.embedding for item in resp.data]