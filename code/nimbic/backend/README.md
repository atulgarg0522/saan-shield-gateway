# saan-ai-gateway Backend

FastAPI Python application that implements the core gateway business logic, routing of prompts to different LLM providers, caching using Redis, telemetry tracing, and analytical database tracking.

## Architecture & Scaffolding

- **`app/main.py`:** Application entry point. Sets up the FastAPI app, middleware, routes, and hooks into structlog structured JSON logging.
- **`app/config.py`:** Settings parser powered by `pydantic-settings` to enforce strict type checking and loading from environment variables.
- **`app/database.py`:** Asynchronous database engine setup using SQLAlchemy 2.0 with the `asyncpg` driver for PostgreSQL.
- **`app/redis.py`:** Connection pool and async client for Redis using `redis.asyncio` (formerly `aioredis`).
- **`tests/`:** Test suite with `pytest-asyncio` setups.

## Setup & Running

1. **Virtual Environment Setup:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate # or .venv\Scripts\activate on Windows
   ```

2. **Install Dependencies:**
   ```bash
   pip install -e ".[dev]"
   ```

3. **Running local development server:**
   ```bash
   uvicorn app.main:app --reload
   ```

4. **Running Tests:**
   ```bash
   pytest
   ```
