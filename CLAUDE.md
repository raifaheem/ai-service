# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**health-ai-service** is a FastAPI-based backend service that provides medical consultation via an AI assistant with RAG (Retrieval-Augmented Generation) capabilities. The service is built with Python 3.11 and uses OpenAI's API for LLM and embeddings, Qdrant for vector search, and Redis for conversation memory.

**Type**: Python backend service  
**Framework**: FastAPI with async/await  
**Main Language**: Russian/English/Kazakh (multilingual support)

## Quick Commands

### Setup & Development
```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env with your credentials (OPENAI_API_KEY, SERVICE_TOKEN, etc.)

# Run locally with Uvicorn
uvicorn app.main:app --reload --port 8001

# Run with Docker Compose (includes Redis and Qdrant)
docker-compose up -d
```

### Testing
```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_health.py -v

# Run with coverage
pytest --cov=app tests/
```

### Development Services
The project includes docker-compose with three services:
- **Redis**: Stores conversation history (port 6379, requires password)
- **Qdrant**: Vector database for RAG (port 6333)
- **AI Service**: FastAPI app (port 8001)

All services have health checks configured. Use `.env.docker` for Docker environment variables.

## Architecture

### High-Level Flow

The service implements a medical AI assistant with memory and RAG:

1. **Chat Request** → Authentication (JWT or Service Token) → Rate limiting
2. **Memory Retrieval** → Fetch previous conversation turns from Redis
3. **RAG Retrieval** → Semantic search in Qdrant for relevant medical articles
4. **LLM Generation** → OpenAI API (gpt-4o-mini) with system prompt, history, and RAG context
5. **Response** → Stream or direct response with conversation persistence

Both streaming (`/v1/chat/stream`) and non-streaming (`/v1/chat`) endpoints are supported.

### Key Components

#### Routers (`app/routers/`)
- **chat.py**: Core chat endpoints with streaming support. Handles authentication, rate limiting, RAG context building, and conversation persistence.
- **conversations.py**: Retrieve/delete conversation history by conversation_id.
- **articles.py**: Upload and analyze medical articles, chunk them, and index into vector DB.
- **rag.py**: Dev-only RAG management endpoints (enabled via `ENABLE_DEV_ROUTES`).

#### Services (`app/services/`)
- **llm.py**: OpenAI integration. Builds system prompts with RAG context, handles temperature/max_tokens, supports streaming.
- **rag.py**: Retrieves semantically similar chunks and formats them for LLM context.
- **vector_store.py**: Qdrant operations (collection ensure, upsert, search with language filtering).
- **embeddings.py**: OpenAI embedding models, supports text-embedding-3-small/large and ada-002.
- **memory.py**: Redis-backed conversation history (Turn dataclass, atomic list operations with TTL/max turns).
- **rate_limit.py**: Per-minute rate limiting keyed by user_id/service/IP using Redis INCR.
- **redis_client.py & vector_client.py**: Singleton connection management with fail-fast initialization.
- **article_parser.py**: Text chunking strategy for articles.
- **article_analyzer.py**: LLM-based article analysis.
- **file_text_extract.py**: PDF/DOCX text extraction.
- **i18n.py**: Locale normalization and prompt/disclaimer retrieval (ru/en/kk).

#### Schemas & Config
- **schemas.py**: Core Pydantic models (ChatRequest, ChatResponse, UserProfile).
- **schemas_articles.py & schemas_rag.py**: Article-specific request/response models.
- **config.py**: Pydantic Settings with environment variable loading.
- **prompts.py**: System prompts and disclaimers in three languages.
- **security.py**: Auth guard supporting JWT and service token authentication.

### Data Flow for Chat

1. User sends message with optional conversation_id, profile (age/sex/conditions/goals), history, locale.
2. If no history provided, fetch from Redis (last 12 turns, configurable).
3. RAG: Embed query, search Qdrant with language filter → extract up to 5 chunks.
4. LLM: Build messages with system prompt + RAG instruction + history + user message.
5. OpenAI streaming/non-streaming → aggregate deltas → append to Redis history.
6. Return response with conversation_id, sources, rag_used flag, and medical disclaimer.

### Configuration via Environment

Key settings (see .env.example):
- **OpenAI**: `OPENAI_API_KEY`, `OPENAI_MODEL` (gpt-4o-mini), `OPENAI_EMBEDDING_MODEL` (text-embedding-3-small)
- **Redis**: `REDIS_URL`, `REDIS_TTL_SECONDS` (86400), `REDIS_MAX_TURNS` (12)
- **Qdrant**: `QDRANT_URL`, `QDRANT_COLLECTION` (medical_articles)
- **Auth**: `SERVICE_TOKEN` (for service-to-service), `JWT_PUBLIC_KEY`, `JWT_ALG` (RS256)
- **Limits**: `RATE_LIMIT_PER_MINUTE` (20), `RATE_LIMIT_BURST` (5)
- **Dev**: `ENABLE_DEV_ROUTES` (toggles /v1/rag endpoints)

## Common Development Patterns

### Adding a New Chat Feature

1. Extend `ChatRequest` schema in `schemas.py` if new input is needed.
2. Extract and process the input in `chat.py` endpoint.
3. If it affects LLM prompt, modify `prompts.py` or adjust prompt building in `llm.py`.
4. Update tests with new request structure.

### Working with Conversations

- Conversation ID is UUID-4, auto-generated or client-provided.
- All conversation data lives in Redis under key `{REDIS_PREFIX}:conv:{conversation_id}:turns`.
- History is stored as JSON-serialized Turn objects, kept in order (old to new).
- Redis TTL is per-conversation; explicit delete via conversation router.

### Multilingual Support

- Locales: "ru" (Russian), "en" (English), "kk" (Kazakh). Defaults to "ru" if unrecognized.
- Prompts are keyed by locale in `prompts.py`. Update all three when changing instructions.
- RAG instructions and disclaimers follow the same pattern.
- Chat request accepts `locale` field; normalized in router before passing to services.

### Rate Limiting

- Implemented as Redis INCR + EXPIRE on minute-level sliding window.
- Identifier computed from: JWT `sub` → Service auth + X-User-Id header → Client IP.
- Limit = `RATE_LIMIT_PER_MINUTE + RATE_LIMIT_BURST` (25 by default).
- 429 Too Many Requests on overage.

### Adding Articles & RAG

- `/v1/articles/upload` endpoint accepts file or raw text.
- Articles are chunked, embedded, and upserted to Qdrant.
- Search filters by language during RAG retrieval.
- Each chunk has metadata (type, chunk_index, total_chunks, source_id for deduplication).

## Testing Notes

- Tests use `pytest` with `asyncio_mode = auto` (pytest.ini).
- Environment is set via `conftest.py` (test-friendly API key and Redis/Qdrant URLs).
- Health endpoint test is basic; integration tests need live services.

## Deployment Notes

- Dockerfile: Python 3.11-slim, copies requirements and app/, exposes 8001.
- docker-compose: Services depend on health checks; ai service waits for Redis and Qdrant.
- Environment files: `.env` for local, `.env.docker` for Docker.
- Health check endpoint: GET `/health` returns service status and config.

## Tools
- Use Context7 tool to see libraries docs if needed