import time
import pytest
from httpx import AsyncClient
from app.main import app
from app.middleware.rate_limit import RateLimiter


@pytest.mark.asyncio
async def test_public_health_endpoints(client: AsyncClient):
    """
    Verifies that health check routes are public, unauthenticated,
    and return standard diagnostics variables.
    """
    # 1. Test basic health endpoint
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"
    assert "uptime_seconds" in data
    assert data["uptime_seconds"] >= 0

    # 2. Test detailed health endpoint
    # The database and redis dependencies will be processed by the test context
    response_detailed = await client.get("/health/detailed")
    assert response_detailed.status_code == 200
    data_detailed = response_detailed.json()
    assert "status" in data_detailed
    assert "db" in data_detailed
    assert "redis" in data_detailed
    assert data_detailed["version"] == "0.1.0"


@pytest.mark.asyncio
async def test_request_id_header_injection(client: AsyncClient):
    """
    Verifies that the Request ID middleware intercepts queries, assigns
    a unique request tracking UUID, and returns it inside the response headers.
    """
    response = await client.get("/health")
    assert response.status_code == 200
    assert "X-Request-ID" in response.headers
    
    # Assert UUID structure (8-4-4-4-12 characters)
    req_id = response.headers["X-Request-ID"]
    assert len(req_id) == 36
    assert req_id.count("-") == 4
