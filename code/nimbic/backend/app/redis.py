from typing import AsyncGenerator
import redis.asyncio as aioredis
from app.config import settings

# Create a connection pool and client from connection URL.
# decode_responses=True automatically decodes bytes from redis to utf-8 strings.
redis_client: aioredis.Redis = aioredis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
)


# FastAPI Dependency to get redis client instance
async def get_redis() -> AsyncGenerator[aioredis.Redis, None]:
    yield redis_client
