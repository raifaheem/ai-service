# 🏥 Health AI Service — Ультимативный план завершения и выхода в продакшн

## Контекст проекта

**Проект:** `health-ai-service` — FastAPI микросервис когнитивного AI-ассистента по здоровью  
**Стек:** Python 3.11, FastAPI, OpenAI API (gpt-4o-mini), Qdrant (vector DB), Redis (memory), Docker  
**Роль в системе:** AI-модуль, который подключается к Laravel-бэкенду через REST API (Service Token auth)  
**Текущая версия:** MVP с базовым чатом, RAG, стримингом, i18n (ru/en/kk), article analyzer  

---

## Аудит текущего состояния

### ✅ Что уже реализовано
- FastAPI приложение с lifespan-менеджментом (Redis + Qdrant init/close)
- Два chat-эндпоинта: `/v1/chat` (sync) и `/v1/chat/stream` (SSE)
- RAG-пайплайн: embeddings (text-embedding-3-small) → Qdrant → контекст в промпт
- Redis-память разговоров (12 последних turns, TTL, owner-привязка)
- Загрузка/анализ медицинских статей (txt/pdf/docx → chunking → indexing)
- i18n на 3 языка (ru/en/kk) — промпты, disclaimers, RAG-инструкции
- Аутентификация: JWT (RS256) + Service Token
- Rate limiting (per-minute sliding window через Redis)
- Профиль пользователя (age/sex/conditions/goals)
- Docker + docker-compose (Redis, Qdrant, AI Service)
- Health check endpoint с проверкой Redis и Qdrant
- Базовые тесты (6 файлов, ~40% покрытие)

### ❌ Что отсутствует / требует доработки
- Системные промпты слишком короткие и примитивные для "когнитивного" ассистента
- Нет классификации намерений пользователя (intent detection)
- Нет структурированного извлечения симптомов/условий из сообщений
- Нет логики уточняющих вопросов (follow-up)
- Нет системы триажа и обнаружения red-flags
- Нет защиты от prompt injection
- Нет суммаризации длинных разговоров
- Нет порога релевантности для RAG (возвращает даже нерелевантные чанки)
- Нет кэширования embeddings
- Нет retry-логики для OpenAI API
- Нет structured logging и метрик
- Нет CI/CD пайплайна
- Тесты покрывают только базовые кейсы
- CORS настроен на `*`
- Нет OpenAPI-документации (описания, примеры)
- Профиль пользователя не персонализирует ответы (просто текст в промпт)
- Docker-образ не оптимизирован (нет multi-stage build)
- Нет graceful shutdown для streaming connections

---

## ПЛАН ЗАВЕРШЕНИЯ — 12 ФАЗ

---

### ФАЗА 1: Когнитивные системные промпты (КРИТИЧЕСКАЯ)

**Цель:** Превратить базового бота в когнитивного медицинского ассистента с глубоким, структурированным подходом к здоровью.

**Файлы:** `app/prompts.py`

**Задачи:**

1. **Переписать `SYSTEM_PROMPTS` для всех 3 локалей**, сделав их детальными (300-500 слов каждый). Каждый промпт должен включать:

   - **Роль и идентичность:** "Ты — когнитивный AI-ассистент по здоровью и здоровому образу жизни. Ты помогаешь пользователям понимать своё здоровье, отвечаешь на вопросы, даёшь рекомендации по образу жизни, питанию, физической активности, сну, ментальному здоровью."

   - **Когнитивная модель рассуждения (ключевое отличие).** Промпт должен заставить модель думать структурировано:
     ```
     При ответе на вопрос о симптомах следуй этой структуре:
     1. Уточни контекст (давность, интенсивность, сопутствующие факторы)
     2. Рассмотри наиболее вероятные и безопасные объяснения
     3. Укажи, когда стоит обратиться к врачу (красные флаги)
     4. Дай практические рекомендации по облегчению состояния
     5. Всегда заканчивай дисклеймером
     ```

   - **Персонализация:** "Если предоставлен профиль пользователя (возраст, пол, хронические заболевания, цели), адаптируй ответ: учитывай возрастные особенности, противопоказания при хронических заболеваниях, прогресс к целям."

   - **Ограничения безопасности:**
     ```
     АБСОЛЮТНЫЕ ЗАПРЕТЫ:
     - Никогда не ставь конкретный диагноз
     - Никогда не отменяй назначения врача
     - Никогда не рекомендуй конкретные лекарства с дозировками
     - При описании тревожных симптомов (боль в груди, затруднённое дыхание, 
       потеря сознания, кровотечение, суицидальные мысли) — НЕМЕДЛЕННО 
       рекомендуй вызвать скорую или обратиться в приёмный покой
     - Не давай советы по лечению детей до 3 лет — направляй к педиатру
     ```

   - **Стиль общения:**
     ```
     - Используй тёплый, но профессиональный тон
     - Избегай медицинского жаргона без пояснений
     - Структурируй длинные ответы с подзаголовками
     - Если пользователь тревожится — сначала успокой, потом информируй
     - Задавай уточняющие вопросы, если информации недостаточно
     ```

   - **Работа с RAG-контекстом:** "Когда предоставлен контекст из базы знаний, обязательно ссылайся на источники. Если контекст противоречит твоим знаниям — укажи на это и порекомендуй консультацию специалиста."

2. **Создать специализированные промпты** для разных типов запросов (добавить в `prompts.py`):
   - `SYMPTOM_CHECK_ADDON` — дополнение для запросов о симптомах
   - `LIFESTYLE_ADDON` — дополнение для вопросов о ЗОЖ/питании/спорте
   - `MENTAL_HEALTH_ADDON` — дополнение для вопросов о ментальном здоровье
   - `EMERGENCY_ADDON` — дополнение при обнаружении экстренных симптомов

3. **Интернационализировать** все новые промпты на ru/en/kk

---

### ФАЗА 2: Классификация намерений и маршрутизация (КРИТИЧЕСКАЯ)

**Цель:** Определять тип запроса пользователя и выбирать оптимальную стратегию ответа.

**Новые файлы:** `app/services/intent.py`

**Задачи:**

1. **Создать `app/services/intent.py`** с функцией классификации намерения:

   ```python
   # Структура IntentResult
   class IntentResult:
       category: str          # symptom_check | lifestyle | nutrition | mental_health | 
                               # fitness | sleep | emergency | general_health | off_topic
       confidence: float      # 0.0 - 1.0
       requires_followup: bool # нужны ли уточняющие вопросы
       detected_entities: dict # извлечённые сущности (симптомы, части тела, и т.д.)
       risk_level: str        # low | medium | high | emergency
   ```

2. **Реализовать быструю классификацию** через один LLM-вызов с `response_format=json_object`:
   - На вход: сообщение пользователя + последние 2 сообщения из истории (для контекста)
   - На выход: JSON с полями category, confidence, risk_level, detected_entities, requires_followup
   - Использовать `temperature=0.1` и `max_tokens=300` для скорости
   - Модель: та же `settings.openai_model`

3. **Интегрировать в `chat.py`:** вызывать `classify_intent()` перед основным LLM-вызовом. На основе результата:
   - Выбирать дополнительный промпт-аддон (из Фазы 1)
   - Устанавливать `temperature` (ниже для медицинских, выше для lifestyle)
   - Если `risk_level == "emergency"` — форсировать ответ с рекомендацией экстренной помощи
   - Если `category == "off_topic"` — вежливо перенаправить к теме здоровья
   - Если `requires_followup == true` — добавить в промпт инструкцию задать уточняющий вопрос

4. **Добавить поле `intent` в `ChatResponse`** (опционально, для фронтенда):
   ```python
   class ChatIntent(BaseModel):
       category: str
       risk_level: str
       confidence: float
   ```

5. **Кэшировать результаты классификации** в Redis на 5 минут (по хешу сообщения) для экономии токенов при повторных/похожих запросах

---

### ФАЗА 3: Улучшение RAG-пайплайна

**Цель:** Повысить качество и релевантность контекста из базы знаний.

**Файлы:** `app/services/rag.py`, `app/services/vector_store.py`, `app/services/embeddings.py`

**Задачи:**

1. **Добавить порог релевантности (score threshold):**
   - В `search_text_chunks()` фильтровать результаты с `score < 0.35` (COSINE distance)
   - Добавить настройку `RAG_SCORE_THRESHOLD` в `config.py` (default: 0.35)
   - Если после фильтрации 0 чанков — не добавлять RAG-контекст в промпт

2. **Реализовать гибридный поиск (keyword + semantic):**
   - Добавить payload-индекс в Qdrant для полнотекстового поиска
   - Использовать Qdrant's Query API для совмещения vector + text search
   - Или: сделать простой keyword pre-filter через Redis (хранить title/keywords каждого чанка)

3. **Кэширование embeddings:**
   - В `embeddings.py` добавить Redis-кэш для embed_text():
     ```
     key: healthai:emb:<md5(text)>
     value: JSON-сериализованный вектор
     TTL: 24 часа
     ```
   - Это критично для экономии API-вызовов при повторных запросах

4. **Улучшить chunking стратегию в `article_parser.py`:**
   - Разбивать по параграфам, а не просто по символам
   - Сохранять заголовок секции в метаданных каждого чанка
   - Увеличить overlap до 200 символов для лучшего контекста

5. **Добавить поле `rag_score` в ответ** — средний score использованных чанков для мониторинга качества

6. **Мультиязычный поиск:** если результатов на языке запроса мало (<2), дополнительно искать на ru/en и помечать источники как переводные

---

### ФАЗА 4: Улучшение памяти и контекста разговора

**Цель:** Сделать разговоры более связными и контекстуальными.

**Файлы:** `app/services/memory.py`, `app/routers/chat.py`

**Задачи:**

1. **Суммаризация длинных разговоров:**
   - Когда количество turns > 8, суммировать старые turns (с 1 по N-6) в один "summary turn"
   - Создать `app/services/summarizer.py`:
     ```python
     async def summarize_conversation(turns: list[Turn], locale: str) -> str:
         # LLM-вызов для создания краткого пересказа
         # "Summarize the key medical context from this conversation:
         #  symptoms mentioned, conditions discussed, recommendations given"
     ```
   - Хранить summary в Redis под отдельным ключом: `{prefix}:conv:{id}:summary`
   - При построении промпта: [system] + [summary если есть] + [последние 6 turns] + [user message]

2. **Извлечение медицинского контекста из разговора:**
   - После каждого ответа ассистента, извлекать и сохранять:
     ```python
     class ConversationContext:
         mentioned_symptoms: list[str]
         mentioned_conditions: list[str]
         recommendations_given: list[str]
         risk_factors: list[str]
         follow_up_needed: bool
     ```
   - Хранить в Redis как JSON, обновлять инкрементально
   - Передавать этот контекст в промпт при следующих запросах

3. **Улучшить `profile_to_text()` в `chat.py`:**
   - Локализовать (сейчас всегда на русском, даже для en/kk)
   - Добавить больше полей профиля:
     ```python
     class UserProfile(BaseModel):
         age: Optional[int]
         sex: Optional[str]
         conditions: Optional[List[str]]
         goals: Optional[List[str]]
         allergies: Optional[List[str]]       # НОВОЕ
         medications: Optional[List[str]]     # НОВОЕ  
         height_cm: Optional[int]             # НОВОЕ
         weight_kg: Optional[float]           # НОВОЕ
         activity_level: Optional[str]        # НОВОЕ: sedentary|light|moderate|active
     ```

4. **Conversation metadata:**
   - Сохранять метаданные разговора (topic, начало, количество turns) в Redis
   - Эндпоинт `/v1/conversations/{id}/metadata` для Laravel

---

### ФАЗА 5: Безопасность и защита

**Цель:** Защитить сервис от злоупотреблений и обеспечить медицинскую безопасность ответов.

**Новые файлы:** `app/services/safety.py`, `app/services/content_filter.py`

**Задачи:**

1. **Prompt Injection Protection (`app/services/safety.py`):**
   ```python
   async def sanitize_user_input(message: str) -> str:
       # Удалять/экранировать попытки инъекции:
       # - "Ignore previous instructions..."
       # - "You are now a different AI..."
       # - Markdown/HTML injection
       # - Экранирование спецсимволов, которые могут нарушить промпт
   
   async def detect_injection_attempt(message: str) -> bool:
       # Лёгкая проверка на паттерны инъекций
       # Если True — логировать и отвечать стандартным отказом
   ```

2. **Медицинская безопасность (`app/services/content_filter.py`):**
   ```python
   async def check_response_safety(response: str, locale: str) -> tuple[str, list[str]]:
       # Проверить ответ на:
       # - Конкретные дозировки лекарств → заменить на "проконсультируйтесь с врачом"
       # - Диагнозы, утверждаемые как факт → смягчить формулировки
       # - Отсутствие дисклеймера → добавить
       # Возвращает: (filtered_response, list_of_applied_filters)
   ```

3. **Red-flag detection:**
   - В `intent.py` (Фаза 2) добавить список экстренных ключевых слов/фраз
   - При обнаружении → форсировать emergency response с номерами экстренных служб
   - Локализованные номера: 103/112 (KZ), 911 (US), 112 (EU)

4. **CORS hardening в `main.py`:**
   - Для production: `ALLOWED_ORIGINS` должен содержать конкретные домены Laravel-приложения
   - Добавить проверку в config: если `APP_ENV == "production" and ALLOWED_ORIGINS == "*"` → warning в логах

5. **Input validation:**
   - Добавить максимальную длину `conversation_id` (36 символов, UUID формат)
   - Валидировать `profile.conditions` и `profile.goals` — максимум 20 элементов, 200 символов каждый
   - Ограничить размер `metadata` до 5KB

6. **Добавить API key rotation support:**
   - Поддержка нескольких SERVICE_TOKEN через список (для бесшовной ротации)

---

### ФАЗА 6: Надёжность и отказоустойчивость

**Цель:** Сервис должен работать стабильно в production-нагрузках.

**Файлы:** `app/services/llm.py`, `app/services/redis_client.py`, `app/services/vector_client.py`, `app/config.py`

**Задачи:**

1. **Retry-логика для OpenAI API (`app/services/llm.py`):**
   ```python
   # Использовать tenacity или встроенный retry в openai SDK
   # openai Python SDK v1+ уже имеет встроенные retries:
   client = AsyncOpenAI(
       api_key=settings.openai_api_key,
       max_retries=3,           # ДОБАВИТЬ
       timeout=30.0,            # ДОБАВИТЬ
   )
   ```

2. **Timeout для LLM-вызовов:**
   - Добавить `OPENAI_TIMEOUT_SECONDS` в config (default: 30)
   - Обернуть streaming в `asyncio.wait_for()` с общим таймаутом 60 секунд

3. **Circuit breaker для внешних сервисов:**
   - Если OpenAI возвращает 5xx 3 раза подряд → переключиться в degraded mode на 60 секунд
   - В degraded mode: отвечать "Сервис временно перегружен, попробуйте позже"
   - Добавить соответствующий статус в `/health`

4. **Graceful shutdown:**
   - В `lifespan()` добавить ожидание завершения активных streaming-соединений
   - Timeout для shutdown: 30 секунд

5. **Redis connection pool:**
   ```python
   _redis = redis.from_url(
       settings.redis_url,
       encoding="utf-8",
       decode_responses=True,
       max_connections=20,      # ДОБАВИТЬ
       retry_on_timeout=True,   # ДОБАВИТЬ
       socket_timeout=5,        # ДОБАВИТЬ
       socket_connect_timeout=5 # ДОБАВИТЬ
   )
   ```

6. **Fallback при недоступности RAG:**
   - Если Qdrant недоступен → отвечать без RAG-контекста (уже частично работает)
   - Логировать warning, не прерывать запрос
   - Добавить `rag_available: bool` в ответ

7. **Добавить конфигурации в `config.py`:**
   ```python
   openai_timeout: int = Field(default=30, alias="OPENAI_TIMEOUT_SECONDS")
   openai_max_retries: int = Field(default=3, alias="OPENAI_MAX_RETRIES")
   rag_score_threshold: float = Field(default=0.35, alias="RAG_SCORE_THRESHOLD")
   max_response_tokens: int = Field(default=1000, alias="MAX_RESPONSE_TOKENS")
   ```

---

### ФАЗА 7: Логирование, мониторинг, наблюдаемость

**Цель:** Полная видимость работы сервиса в production.

**Новые файлы:** `app/logging_config.py`, `app/middleware/request_logging.py`

**Задачи:**

1. **Structured logging (`app/logging_config.py`):**
   ```python
   # Настроить JSON-логирование для production
   # Каждый лог-entry должен содержать:
   # - timestamp, level, message
   # - request_id (UUID на каждый запрос)
   # - conversation_id (если есть)
   # - user_id (если есть)
   # - duration_ms
   # - OpenAI tokens used (prompt + completion)
   
   # Использовать structlog или python-json-logger
   # Добавить в requirements.txt: structlog>=24.0.0
   ```

2. **Request logging middleware (`app/middleware/request_logging.py`):**
   ```python
   # Middleware для логирования каждого запроса:
   # - HTTP method, path, status code
   # - Response time
   # - Client IP (для rate-limit debugging)
   # - Request size / Response size
   ```

3. **Метрики для мониторинга (добавить в `/health` или отдельный `/metrics`):**
   ```python
   # Собирать и отдавать метрики:
   metrics = {
       "requests_total": int,           # общее число запросов
       "requests_by_intent": dict,      # распределение по категориям
       "avg_response_time_ms": float,   # среднее время ответа
       "openai_tokens_total": int,      # использованные токены
       "rag_hit_rate": float,           # % запросов с полезным RAG
       "active_conversations": int,     # активных разговоров в Redis
       "error_rate_1h": float,          # процент ошибок за час
       "qdrant_collection_size": int,   # размер коллекции
   }
   ```

4. **Добавить request_id:**
   - Генерировать UUID для каждого входящего запроса
   - Пробрасывать через все сервисы для tracing
   - Возвращать в заголовке ответа `X-Request-Id`

5. **Логирование OpenAI usage:**
   - После каждого LLM-вызова логировать: model, prompt_tokens, completion_tokens, duration
   - Для streaming: собирать usage из финального chunk'а (уже реализовано в `stream_health_answer`)

---

### ФАЗА 8: Тестирование

**Цель:** Довести покрытие до 80%+, добавить интеграционные и нагрузочные тесты.

**Файлы:** `tests/`

**Задачи:**

1. **Unit-тесты (добавить):**
   ```
   tests/
   ├── test_intent.py          # Тесты классификации намерений
   ├── test_safety.py          # Тесты sanitize_user_input, detect_injection
   ├── test_content_filter.py  # Тесты фильтрации ответов
   ├── test_summarizer.py      # Тесты суммаризации
   ├── test_rag.py             # Тесты RAG score threshold, compress_sources
   ├── test_memory.py          # Тесты memory service (mock Redis)
   ├── test_llm.py             # Тесты построения промптов
   ├── test_rate_limit.py      # Тесты rate limiter
   ├── test_security.py        # Тесты auth_guard, resolve_user_id
   ├── test_schemas.py         # Тесты валидации Pydantic моделей
   ├── test_prompts.py         # Тесты что все промпты заполнены для всех локалей
   ```

2. **Интеграционные тесты (с docker-compose):**
   ```
   tests/integration/
   ├── test_chat_flow.py       # Полный цикл: запрос → intent → RAG → LLM → memory → ответ
   ├── test_article_pipeline.py # Upload → chunk → index → search
   ├── test_streaming.py       # SSE streaming + memory persistence
   ├── test_conversation.py    # Create → get → delete conversation
   ```

3. **Тесты безопасности:**
   ```
   tests/security/
   ├── test_prompt_injection.py # Попытки инъекций через user message
   ├── test_auth.py            # Неверные токены, просроченные JWT
   ├── test_rate_limit.py      # Превышение лимита
   ├── test_input_validation.py # Огромные запросы, невалидные данные
   ```

4. **Обновить `conftest.py`:**
   - Добавить фикстуры для мокирования OpenAI, Redis, Qdrant
   - Фикстура для создания TestClient с авторизацией
   - Фикстура для sample UserProfile, ChatRequest

5. **Настроить pytest-cov:**
   - Добавить в `pytest.ini`:
     ```ini
     [pytest]
     asyncio_mode = auto
     addopts = --cov=app --cov-report=term-missing --cov-fail-under=80
     ```

---

### ФАЗА 9: API-документация и контракт с Laravel

**Цель:** Чёткий API-контракт для интеграции с Laravel-бэкендом и Swift-приложением.

**Файлы:** `app/main.py`, `app/schemas.py`, все роутеры

**Задачи:**

1. **OpenAPI описания для всех эндпоинтов:**
   ```python
   app = FastAPI(
       title="Health AI Service",
       description="Когнитивный AI-ассистент по здоровью с RAG и персонализацией",
       version=settings.app_version,
       docs_url="/docs" if settings.app_env != "production" else None,
       redoc_url="/redoc" if settings.app_env != "production" else None,
   )
   ```

2. **Добавить `summary` и `description` к каждому роутеру:**
   ```python
   @router.post("/chat", 
       response_model=ChatResponse,
       summary="Отправить сообщение AI-ассистенту",
       description="Основной эндпоинт чата. Поддерживает профиль пользователя, историю, RAG-контекст.")
   ```

3. **Добавить примеры в Pydantic-схемы:**
   ```python
   class ChatRequest(BaseModel):
       message: str = Field(..., min_length=1, max_length=4000, 
           json_schema_extra={"examples": ["У меня болит голова уже 3 дня"]})
       # ...
       
       model_config = ConfigDict(json_schema_extra={
           "examples": [{
               "message": "Какие витамины пить весной?",
               "locale": "ru",
               "profile": {"age": 30, "sex": "female", "goals": ["immunity"]},
           }]
       })
   ```

4. **Создать `API_CONTRACT.md`** — документ для команды:
   - Все эндпоинты с curl-примерами
   - Формат аутентификации (X-Service-Token для Laravel, JWT для прямого доступа)
   - Формат SSE-событий (meta, delta, final, error)
   - Коды ошибок и их значения
   - Пример интеграции из Laravel (PHP Guzzle/Http client)

5. **Версионирование API:**
   - Текущий префикс `/v1` уже есть — хорошо
   - Добавить заголовок `X-API-Version` в ответы

---

### ФАЗА 10: Docker и деплоймент

**Цель:** Production-ready Docker-образ и конфигурация.

**Файлы:** `Dockerfile`, `docker-compose.yml`, `docker-compose.prod.yml`

**Задачи:**

1. **Оптимизировать Dockerfile (multi-stage build):**
   ```dockerfile
   # Stage 1: dependencies
   FROM python:3.11-slim AS builder
   WORKDIR /app
   COPY requirements.txt .
   RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

   # Stage 2: runtime
   FROM python:3.11-slim
   COPY --from=builder /install /usr/local
   WORKDIR /app
   COPY app ./app
   RUN groupadd -r appgroup && useradd -r -g appgroup appuser
   USER appuser
   EXPOSE 8001
   
   # Gunicorn с Uvicorn workers для production
   CMD ["gunicorn", "app.main:app", \
        "-w", "4", \
        "-k", "uvicorn.workers.UvicornWorker", \
        "--bind", "0.0.0.0:8001", \
        "--timeout", "120", \
        "--graceful-timeout", "30", \
        "--access-logfile", "-"]
   ```

2. **Добавить `gunicorn` в requirements.txt**

3. **Создать `docker-compose.prod.yml`:**
   ```yaml
   # Production overrides:
   # - Ограничения ресурсов (memory limits)
   # - Logging driver (json-file с ротацией)
   # - Restart policies
   # - Health check intervals
   # - Volume backups для Redis и Qdrant
   # - НЕ expose ports Qdrant и Redis наружу
   ```

4. **Environment-specific конфигурация:**
   - `.env.production` — шаблон для production
   - Checklist:
     ```
     APP_ENV=production
     ENABLE_DEV_ROUTES=false
     ALLOWED_ORIGINS=https://yourdomain.com
     REDIS_TTL_SECONDS=86400  # 24 часа
     RATE_LIMIT_PER_MINUTE=20
     ```

5. **Добавить `.dockerignore` улучшения:**
   ```
   .git
   .idea
   __pycache__
   *.pyc
   tests/
   .env
   .env.*
   *.md
   ```

---

### ФАЗА 11: CI/CD пайплайн

**Цель:** Автоматизировать тестирование и деплой.

**Новые файлы:** `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`

**Задачи:**

1. **CI пайплайн (`.github/workflows/ci.yml`):**
   ```yaml
   name: CI
   on: [push, pull_request]
   jobs:
     test:
       runs-on: ubuntu-latest
       services:
         redis:
           image: redis:7-alpine
           ports: ["6379:6379"]
         qdrant:
           image: qdrant/qdrant:latest
           ports: ["6333:6333"]
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-python@v5
           with: { python-version: "3.11" }
         - run: pip install -r requirements.txt
         - run: pytest --cov=app --cov-fail-under=80
         
     lint:
       runs-on: ubuntu-latest
       steps:
         - run: pip install ruff mypy
         - run: ruff check app/
         - run: mypy app/ --ignore-missing-imports
         
     security:
       runs-on: ubuntu-latest
       steps:
         - run: pip install bandit safety
         - run: bandit -r app/
         - run: safety check -r requirements.txt
   ```

2. **CD пайплайн (`.github/workflows/deploy.yml`):**
   ```yaml
   # При push в main:
   # 1. Build Docker image
   # 2. Push to container registry
   # 3. Deploy to server (SSH или Kubernetes)
   # 4. Health check после деплоя
   # 5. Rollback при failure
   ```

3. **Линтинг и форматирование:**
   - Добавить `ruff` в dev-зависимости
   - Создать `ruff.toml` с настройками
   - Добавить `mypy` type checking (постепенно)

---

### ФАЗА 12: Наполнение базы знаний (RAG)

**Цель:** База знаний должна содержать качественный медицинский контент.

**Задачи:**

1. **Создать скрипт массовой загрузки (`scripts/seed_knowledge_base.py`):**
   ```python
   # Скрипт для загрузки начального набора статей через API
   # Поддержка: директория с .txt/.pdf/.docx файлами
   # Прогресс-бар, логирование, retry при ошибках
   ```

2. **Подготовить минимальный набор медицинских статей** (20-30 статей) по темам:
   - Общие симптомы (головная боль, боль в спине, усталость, бессонница)
   - Питание (витамины, минералы, диеты, водный баланс)
   - Физическая активность (кардио, силовые, растяжка, восстановление)
   - Ментальное здоровье (стресс, тревога, медитация, дыхательные практики)
   - Сезонное здоровье (простуда, аллергия, жара)
   - Женское/мужское здоровье (базовые темы)
   - На всех трёх языках (ru, en, kk) — минимум по 10 на каждом

3. **Добавить скрипт верификации базы знаний:**
   ```python
   # scripts/verify_knowledge_base.py
   # Проверяет:
   # - Количество чанков в Qdrant
   # - Распределение по языкам
   # - Тестовые запросы с проверкой релевантности
   ```

---

## ПРИОРИТЕТЫ И ПОРЯДОК ВЫПОЛНЕНИЯ

```
КРИТИЧЕСКИЙ ПУТЬ (без этого нет продакшна):
  Фаза 1 → Фаза 2 → Фаза 5 → Фаза 6 → Фаза 10

КАЧЕСТВО AI (делает продукт "когнитивным"):
  Фаза 3 → Фаза 4 → Фаза 12

ИНЖЕНЕРНОЕ КАЧЕСТВО (стабильность и поддержка):
  Фаза 7 → Фаза 8 → Фаза 9 → Фаза 11
```

### Рекомендуемый порядок:

| # | Фаза | Приоритет | Оценка (часы) |
|---|-------|-----------|---------------|
| 1 | Когнитивные промпты | 🔴 Критический | 4-6 |
| 2 | Классификация намерений | 🔴 Критический | 6-8 |
| 3 | Безопасность и защита | 🔴 Критический | 4-6 |
| 4 | Надёжность | 🔴 Критический | 3-4 |
| 5 | Улучшение RAG | 🟡 Высокий | 4-6 |
| 6 | Улучшение памяти | 🟡 Высокий | 4-6 |
| 7 | Логирование | 🟡 Высокий | 3-4 |
| 8 | Тестирование | 🟡 Высокий | 6-8 |
| 9 | Docker/деплоймент | 🔴 Критический | 3-4 |
| 10 | API-документация | 🟢 Средний | 2-3 |
| 11 | CI/CD | 🟢 Средний | 2-3 |
| 12 | База знаний | 🟡 Высокий | 4-6 |

**Итого: ~45-64 часа работы**

---

## ЧЕКЛИСТ ПЕРЕД ВЫХОДОМ В ПРОДАКШН

### Безопасность
- [ ] `ALLOWED_ORIGINS` содержит конкретные домены, не `*`
- [ ] `ENABLE_DEV_ROUTES=false`
- [ ] `APP_ENV=production`
- [ ] JWT_PUBLIC_KEY настроен корректно
- [ ] SERVICE_TOKEN — сильный случайный токен (32+ символов)
- [ ] Prompt injection protection активна
- [ ] Input validation на все поля
- [ ] Rate limiting протестирован под нагрузкой
- [ ] HTTPS обеспечен на уровне reverse proxy (nginx)
- [ ] Redis защищён паролем
- [ ] Qdrant API key настроен (если доступен извне)

### Надёжность
- [ ] OpenAI retry-логика работает
- [ ] Timeout на все внешние вызовы
- [ ] Graceful shutdown
- [ ] Health check возвращает корректный статус
- [ ] При падении Qdrant — сервис продолжает работать (без RAG)
- [ ] При падении Redis — понятная ошибка (не silent failure)

### Качество AI
- [ ] Системные промпты протестированы на 50+ типичных запросах
- [ ] Intent classification корректно определяет категории
- [ ] Emergency detection срабатывает на все critical patterns
- [ ] RAG возвращает релевантные результаты (score > threshold)
- [ ] Дисклеймер присутствует в каждом ответе
- [ ] Ответы на off-topic вежливо перенаправляют к здоровью

### Мониторинг
- [ ] Structured logging в JSON
- [ ] Request tracing (request_id)
- [ ] OpenAI token usage логируется
- [ ] Error rate отслеживается
- [ ] `/health` endpoint работает для Docker healthcheck

### Тестирование
- [ ] Unit test coverage >= 80%
- [ ] Интеграционные тесты проходят с docker-compose
- [ ] Security тесты проходят
- [ ] Нагрузочное тестирование проведено (50 concurrent users)

### Деплоймент
- [ ] Docker image собирается и запускается
- [ ] docker-compose.prod.yml протестирован
- [ ] Volumes для Redis и Qdrant настроены (persistence)
- [ ] Resource limits установлены
- [ ] Backup стратегия для Qdrant данных
- [ ] CI pipeline проходит

### Документация
- [ ] API_CONTRACT.md актуален
- [ ] CLAUDE.md обновлён
- [ ] .env.example содержит все переменные
- [ ] README.md с инструкциями по деплою

---

## ИНСТРУКЦИЯ ДЛЯ CLAUDE CODE

Если ты копируешь этот план в Claude Code для выполнения, используй следующий промпт:

```
Прочитай файл PRODUCTION_COMPLETION_PLAN.md в корне проекта.
Выполняй фазы строго по порядку приоритетов.
Перед началом каждой фазы:
1. Прочитай все файлы, которые будут затронуты
2. Убедись что существующие тесты проходят
3. Реализуй изменения
4. Напиши/обнови тесты
5. Проверь что все тесты проходят

Начни с Фазы 1 (Когнитивные промпты).
После каждой фазы — коммит с сообщением "Phase N: <название>".
```

---

*Документ создан на основе полного аудита проекта health-ai-service. Актуален на апрель 2026.*
