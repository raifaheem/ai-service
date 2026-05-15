from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Strict whitelist: any other value (e.g. "prod", "PRODUCTION", "stg") raises
    # ValidationError at startup. Previously this was a free-form str — a typo
    # like APP_ENV=prod silently bypassed every prod-safety guard below.
    app_env: Literal["dev", "staging", "production"] = Field(default="dev", alias="APP_ENV")
    app_name: str = Field(default="health-ai-service", alias="APP_NAME")
    app_version: str = Field(default="1.0.0", alias="APP_VERSION")
    allowed_origins: str = Field(default="*", alias="ALLOWED_ORIGINS")
    # L3: defaults to False — explicit opt-in for the seeder/verifier dev workflow.
    # `_validate_prod_safety` *also* rejects True in production (defense in depth).
    enable_dev_routes: bool = Field(default=False, alias="ENABLE_DEV_ROUTES")

    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    openai_embedding_model: str = Field(
        default="text-embedding-3-small",
        alias="OPENAI_EMBEDDING_MODEL",
    )

    service_token: str = Field(..., alias="SERVICE_TOKEN")  # comma-separated for rotation

    # Separate token for Prometheus /metrics scraping. Vanilla Prometheus cannot
    # send a custom `X-Service-Token` header in scrape_configs, only
    # `Authorization: Bearer <token>`. When this is set, /metrics accepts that
    # Bearer token in addition to the standard auth_guard paths — letting us
    # rotate the scrape credential independently of the Laravel-facing
    # SERVICE_TOKEN. Empty/unset disables the Bearer path on /metrics.
    metrics_scrape_token: str | None = Field(default=None, alias="METRICS_SCRAPE_TOKEN")

    jwt_public_key: str | None = Field(default=None, alias="JWT_PUBLIC_KEY")
    # `JWT_ALG` is intentionally NOT a setting any more — it's hardcoded to RS256 in
    # security.py. Allowing operators to switch to HS256 while keeping the public key
    # in JWT_PUBLIC_KEY is an attack surface (anyone with the public key can sign
    # tokens). PyJWT also rejects this combination, but defense-in-depth.
    jwt_audience: str | None = Field(default=None, alias="JWT_AUDIENCE")
    jwt_issuer: str | None = Field(default=None, alias="JWT_ISSUER")

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

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = Field(default="text", alias="LOG_FORMAT")

    _SERVICE_TOKEN_PLACEHOLDERS = frozenset(
        {
            "",
            "change-me-in-prod",
            "changeme",
            "dev",
            "test",
            "test-token",
            "placeholder",
            # CI smoke-test fixtures — these were leaking into prod-shaped runs
            # because the smoke step in deploy.yml used a literal "ci-smoke" token.
            "ci-smoke",
            "smoke",
            "ci",
        }
    )
    # Tokens shorter than this clearly aren't 32+ random bytes. Caught here so
    # operators can't paste a half-pasted secret without noticing.
    _SERVICE_TOKEN_MIN_LENGTH = 16

    @model_validator(mode="after")
    def _validate_prod_safety(self):
        if self.app_env != "production":
            return self

        if self.enable_dev_routes:
            raise ValueError(
                "ENABLE_DEV_ROUTES must be false when APP_ENV=production — "
                "dev-only /v1/rag/* endpoints would expose internal operations."
            )

        # M5: CORS in production must enumerate allowed origins. `*` is rejected
        # outright — main.py used to silently downgrade `allow_credentials=False`
        # for the `*` case, but that papered over a misconfiguration we'd rather
        # surface at boot. Specific consumer origins keep the credentials path
        # safe and the surface area auditable.
        if (self.allowed_origins or "").strip() == "*":
            raise ValueError(
                "ALLOWED_ORIGINS=* is not allowed when APP_ENV=production. "
                "List the consumer origins explicitly (comma-separated)."
            )

        # Redis must be password-protected in production. Both redis:// and rediss://
        # URLs encode credentials as user:password@host — presence of '@' is the marker.
        redis_url = (self.redis_url or "").strip()
        if redis_url.startswith(("redis://", "rediss://")) and "@" not in redis_url:
            raise ValueError(
                "REDIS_URL must include a password in production "
                "(e.g. redis://:secret@host:6379/0). Unauthenticated Redis is unsafe."
            )

        # SERVICE_TOKEN must not be a known placeholder. Tokens are comma-separated for
        # rotation — all listed values must be non-placeholder.
        tokens = [t.strip() for t in (self.service_token or "").split(",") if t.strip()]
        if not tokens:
            raise ValueError("SERVICE_TOKEN must be set in production.")
        bad = [t for t in tokens if t.lower() in self._SERVICE_TOKEN_PLACEHOLDERS]
        if bad:
            raise ValueError(
                f"SERVICE_TOKEN contains placeholder value(s): {bad}. " "Generate a strong random token for production."
            )
        too_short = [t for t in tokens if len(t) < self._SERVICE_TOKEN_MIN_LENGTH]
        if too_short:
            raise ValueError(
                f"SERVICE_TOKEN entries must be at least {self._SERVICE_TOKEN_MIN_LENGTH} characters in production "
                f"(got entries of length {[len(t) for t in too_short]}). "
                "Generate via `openssl rand -hex 32` or equivalent."
            )

        # S1: when JWT auth is configured (public key set), require both audience
        # and issuer in production. Without these claims the service accepts any
        # RS256 token signed by the same CA as a different downstream consumer.
        if self.jwt_public_key and (not self.jwt_audience or not self.jwt_issuer):
            raise ValueError(
                "JWT_AUDIENCE and JWT_ISSUER are required in production when "
                "JWT_PUBLIC_KEY is set. Without them, tokens minted for a "
                "different consumer of the same key would be accepted here."
            )

        return self


settings = Settings()
