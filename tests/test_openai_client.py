from app.services.openai_client import client, create_openai_client
from app.config import settings


def test_client_has_retry_config():
    assert client.max_retries == settings.openai_max_retries


def test_client_has_timeout_config():
    timeout = client.timeout
    # OpenAI SDK stores timeout as either a float or a Timeout object
    if isinstance(timeout, (int, float)):
        assert timeout == float(settings.openai_timeout)
    else:
        assert timeout.connect == float(settings.openai_timeout)


def test_create_openai_client_returns_instance():
    c = create_openai_client()
    assert c is not None
    assert c.max_retries == settings.openai_max_retries
