from openai import AsyncOpenAI
from ..config import settings


def create_openai_client() -> AsyncOpenAI:
    """Create an AsyncOpenAI client with retry and timeout settings from config."""
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        max_retries=settings.openai_max_retries,
        timeout=float(settings.openai_timeout),
    )


client = create_openai_client()
