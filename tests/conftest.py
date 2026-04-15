import os

os.environ["OPENAI_API_KEY"] = "test-openai-key"
os.environ["SERVICE_TOKEN"] = "test-token"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["QDRANT_URL"] = "http://localhost:6333"