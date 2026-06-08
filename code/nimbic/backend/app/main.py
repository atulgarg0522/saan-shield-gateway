import time
import uuid
import logging
import sys
from fastapi import FastAPI, Depends
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from sqlalchemy.sql import text
import structlog

from app.config import settings
from app.redis import redis_client, get_redis
from app.db.session import engine, get_db

# Import all routers
from app.routers.proxy import router as proxy_router
from app.routers.orgs import router as orgs_router
from app.routers.keys import router as keys_router
from app.routers.providers import router as providers_router
from app.routers.logs import router as logs_router
from app.routers.security import router as security_router
from app.routers.health import router as health_router
from app.routers.routing import router as routing_router
from app.routers.cache import router as cache_router
from app.routers.finops import router as finops_router

# Configure structlog structured logging
processors = [
    structlog.contextvars.merge_contextvars,
    structlog.processors.add_log_level,
    structlog.processors.format_exc_info,
    structlog.processors.TimeStamper(fmt="iso"),
]

if settings.ENVIRONMENT == "dev":
    # Pretty-prints logs locally for readability
    processors.append(structlog.dev.ConsoleRenderer())
else:
    # Outputs strict JSON streams for production platforms
    processors.append(structlog.processors.JSONRenderer())

structlog.configure(
    processors=processors,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# Create FastAPI app instance
app = FastAPI(
    title="SaaN Shield Gateway",
    description="High-performance, robust, and extensible AI Gateway routing LLM prompts.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# 1. Mount Starlette CORS Middleware
if settings.ENVIRONMENT == "dev":
    origins = ["*"]
else:
    # Split the ALLOWED_ORIGINS string by commas in production
    origins = [origin.strip() for origin in settings.ALLOWED_ORIGINS.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 2. Custom Request ID Middleware
class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Generates a unique request-id UUID if not present in the X-Request-ID header.
    Injects it into Starlette request state and response headers.
    """
    async def dispatch(self, request, call_next):
        req_id = request.headers.get("X-Request-ID")
        if not req_id:
            req_id = str(uuid.uuid4())
        
        request.state.request_id = req_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response


# 3. Custom Response Structured Logging Middleware
class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """
    Measures latency for each request and logs details (method, path, status, latency, request_id)
    via structured structlog logging.
    """
    async def dispatch(self, request, call_next):
        start_time = time.perf_counter()
        req_id = getattr(request.state, "request_id", "unknown")

        response = await call_next(request)

        latency = int((time.perf_counter() - start_time) * 1000)
        
        await logger.ainfo(
            "HTTP Request Completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            latency_ms=latency,
            request_id=req_id
        )
        return response


# Add middlewares to FastAPI lifecycle
app.add_middleware(RequestIDMiddleware)
app.add_middleware(StructuredLoggingMiddleware)

# 4. Mount Routers with correct prefixes
app.include_router(proxy_router)  # Specific prefixes (/v1 or /proxy) are defined natively inside proxy.py
app.include_router(orgs_router, prefix="/api/v1")
app.include_router(keys_router, prefix="/api/v1")
app.include_router(providers_router, prefix="/api/v1")
app.include_router(logs_router, prefix="/api/v1")
app.include_router(security_router, prefix="/api/v1")
app.include_router(routing_router, prefix="/api/v1")
app.include_router(cache_router, prefix="/api/v1")
app.include_router(finops_router, prefix="/api/v1")
app.include_router(health_router)  # health mounts prefix '/health' natively


@app.on_event("startup")
async def startup_event():
    await logger.ainfo("Starting SaaN Shield Gateway service...", env=settings.ENVIRONMENT)
    
    # Audit Database Connection
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await logger.ainfo("Database connection audit succeeded.")
    except Exception as e:
        await logger.aerror("Failed to connect to database on startup.", error=str(e))

    # Audit Redis Connection
    try:
        await redis_client.ping()
        await logger.ainfo("Redis connection pool audit succeeded.")
    except Exception as e:
        await logger.aerror("Failed to connect to Redis cache pool on startup.", error=str(e))

    await logger.ainfo("Gateway started.")


@app.on_event("shutdown")
async def shutdown_event():
    await logger.ainfo("Shutting down SaaN Shield Gateway service...")

    # Close database engine pool connections
    await engine.dispose()
    await logger.ainfo("Database connection engine disposed.")

    # Close Redis client connections
    await redis_client.aclose()
    await logger.ainfo("Redis connection pool closed.")
    
    await logger.ainfo("Gateway shutdown completed.")
