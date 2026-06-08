import time
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from app.db.session import get_db
from app.redis import get_redis

router = APIRouter(prefix="/health", tags=["Diagnostics"])

# Record starting timestamp upon module loading
START_TIME = time.time()


@router.get("", status_code=200)
async def health_check():
    """
    Public health check route. Returns general operational status and process uptime.
    Does NOT require authentication.
    """
    uptime = time.time() - START_TIME
    return {
        "status": "ok",
        "version": "0.1.0",
        "uptime_seconds": int(uptime)
    }


@router.get("/detailed", status_code=200)
async def detailed_health_check(
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis)
):
    """
    Detailed health check route. Queries database and cache states.
    Does NOT require authentication.
    """
    db_status = "ok"
    redis_status = "ok"

    # 1. Test database connection
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    # 2. Test Redis connection (utilizing Depends injection)
    try:
        await redis.ping()
    except Exception:
        redis_status = "error"

    # Set overall service status
    overall_status = "ok"
    if db_status == "error" or redis_status == "error":
        overall_status = "degraded"

    return {
        "status": overall_status,
        "db": db_status,
        "redis": redis_status,
        "version": "0.1.0"
    }
