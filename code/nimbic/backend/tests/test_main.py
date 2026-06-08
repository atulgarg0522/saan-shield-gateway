import pytest
from httpx import AsyncClient
from app.main import app
from app.db.session import get_db
from app.redis import get_redis


# --- MOCK CLASSES FOR DATABASE AND REDIS DEPENDENCY OVERRIDES ---

class MockDBSession:
    async def execute(self, statement):
        # Return a simple mock result object to simulate successful execution
        class MockResult:
            pass
        return MockResult()

    async def rollback(self):
        pass

    async def close(self):
        pass


class MockRedisClient:
    async def ping(self) -> bool:
        return True


async def override_get_db():
    yield MockDBSession()


async def override_get_redis():
    yield MockRedisClient()


@pytest.mark.asyncio
async def test_health_check_endpoint(client: AsyncClient):
    """
    Verifies that the /health/detailed route returns proper health status values when active.
    """
    # Register our mock injection overrides
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    try:
        response = await client.get("/health/detailed")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.1.0"
        assert data["db"] == "ok"
        assert data["redis"] == "ok"

    finally:
        # Crucial cleanup: Clear all overrides so we don't leak state into other tests
        app.dependency_overrides.clear()
