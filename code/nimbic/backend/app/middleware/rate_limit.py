import time
from fastapi import Request, HTTPException, Depends
from app.redis import redis_client
from app.config import settings
from app.models.api_key import ApiKey
from app.middleware.auth import get_current_api_key


class RateLimiter:
    """
    High-performance Redis-backed sliding window rate limiter.
    Can be mounted selectively as a FastAPI route dependency (e.g. Depends(RateLimiter())).
    """
    def __init__(self, requests_per_minute: int = None, requests_per_hour: int = None):
        self.requests_per_minute = requests_per_minute or settings.RATE_LIMIT_PER_MINUTE
        self.requests_per_hour = requests_per_hour or settings.RATE_LIMIT_PER_HOUR

    async def __call__(self, request: Request, api_key: ApiKey = Depends(get_current_api_key)):
        # Rate limit per API Key ID
        api_key_id = str(api_key.id)
        now = time.time()

        # 1. Evaluate Minute Limit (60 second window)
        minute_exceeded, retry_minute = await self._is_limit_exceeded(
            key_id=api_key_id,
            window_name="minute",
            limit=self.requests_per_minute,
            window_seconds=60,
            now=now
        )
        if minute_exceeded:
            raise HTTPException(
                status_code=429,
                detail="Too Many Requests: Minute rate limit exceeded.",
                headers={"Retry-After": str(int(retry_minute))}
            )

        # 2. Evaluate Hour Limit (3600 second window)
        hour_exceeded, retry_hour = await self._is_limit_exceeded(
            key_id=api_key_id,
            window_name="hour",
            limit=self.requests_per_hour,
            window_seconds=3600,
            now=now
        )
        if hour_exceeded:
            raise HTTPException(
                status_code=429,
                detail="Too Many Requests: Hour rate limit exceeded.",
                headers={"Retry-After": str(int(retry_hour))}
            )

    async def _is_limit_exceeded(
        self, key_id: str, window_name: str, limit: int, window_seconds: int, now: float
    ) -> tuple[bool, float]:
        """
        Executes sliding window evaluations in Redis via pipelining.
        Calculates exact Retry-After parameters by reading the oldest sorted set score.
        """
        redis_key = f"rate_limit:{key_id}:{window_name}"
        prune_score = now - window_seconds

        # Pipelined batch execution
        pipe = redis_client.pipeline()
        pipe.zremrangebyscore(redis_key, 0, prune_score)
        pipe.zadd(redis_key, {str(now): now})
        pipe.zcard(redis_key)
        pipe.zrange(redis_key, 0, 0, withscores=True)
        # Add 5 seconds buffer to Redis TTL to ensure key cleanup
        pipe.expire(redis_key, window_seconds + 5)

        res = await pipe.execute()

        cardinality = res[2]
        oldest_items = res[3]

        if cardinality > limit:
            # Limit exceeded: calculate when window slots free up
            if oldest_items:
                oldest_score = oldest_items[0][1]
                retry_after = oldest_score + window_seconds - now
                return True, max(1.0, retry_after)
            return True, float(window_seconds)

        return False, 0.0
