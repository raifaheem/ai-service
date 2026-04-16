import pytest


@pytest.mark.asyncio
async def test_api_version_header_on_health(auth_client):
    response = await auth_client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("X-API-Version") == "v1"
    assert response.headers.get("X-Service-Version")


@pytest.mark.asyncio
async def test_api_version_header_on_root(auth_client):
    response = await auth_client.get("/")
    assert response.status_code == 200
    assert response.headers.get("X-API-Version") == "v1"


@pytest.mark.asyncio
async def test_openapi_schema_advertises_description(auth_client):
    response = await auth_client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"].get("description")
    tag_names = {t["name"] for t in schema.get("tags", [])}
    assert {"chat", "conversations", "articles", "system"}.issubset(tag_names)
