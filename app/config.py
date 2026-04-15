from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = Field(default="dev", alias="APP_ENV")
    app_name: str = Field(default="health-ai-service", alias="APP_NAME")
    app_version: str = Field(default="1.0.0", alias="APP_VERSION")
    allowed_origins: str = Field(default="*", alias="ALLOWED_ORIGINS")
    enable_dev_routes: bool = Field(default=True, alias="ENABLE_DEV_ROUTES")

    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    openai_embedding_model: str = Field(
        default="text-embedding-3-small",
        alias="OPENAI_EMBEDDING_MODEL",
    )

    service_token: str = Field(..., alias="SERVICE_TOKEN")  # comma-separated for rotation

    jwt_public_key: str | None = Field(default=None, alias="JWT_PUBLIC_KEY")
    jwt_alg: str = Field(default="RS256", alias="JWT_ALG")

    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    redis_prefix: str = Field(default="healthai", alias="REDIS_PREFIX")
    redis_ttl_seconds: int = Field(default=3600, alias="REDIS_TTL_SECONDS")
    redis_max_turns: int = Field(default=12, alias="REDIS_MAX_TURNS")

    rate_limit_per_minute: int = Field(default=20, alias="RATE_LIMIT_PER_MINUTE")
    rate_limit_burst: int = Field(default=5, alias="RATE_LIMIT_BURST")

    qdrant_url: str = Field(default="http://localhost:6333", alias="QDRANT_URL")
    qdrant_api_key: str | None = Field(default=None, alias="QDRANT_API_KEY")
    qdrant_collection: str = Field(default="medical_articles", alias="QDRANT_COLLECTION")

    qdrant_timeout: int = Field(default=10, alias="QDRANT_TIMEOUT")

    openai_timeout: int = Field(default=30, alias="OPENAI_TIMEOUT_SECONDS")
    openai_max_retries: int = Field(default=3, alias="OPENAI_MAX_RETRIES")
    max_response_tokens: int = Field(default=1000, alias="MAX_RESPONSE_TOKENS")

    redis_max_connections: int = Field(default=20, alias="REDIS_MAX_CONNECTIONS")
    redis_socket_timeout: int = Field(default=5, alias="REDIS_SOCKET_TIMEOUT")

    rag_score_threshold: float = Field(default=0.35, alias="RAG_SCORE_THRESHOLD")
    embedding_cache_ttl: int = Field(default=86400, alias="EMBEDDING_CACHE_TTL")


settings = Settings()
