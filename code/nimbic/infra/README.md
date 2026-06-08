# saan-ai-gateway Infrastructure

This folder maintains configuration files, migration scripts, and build environments for the **saan-ai-gateway** monorepo services.

## Docker Topology

We run our microservices inside a private Docker bridge network (`saan-network`):
1. **`db` (PostgreSQL 16):** Datastore for analytical logs, API key configurations, and routing weights.
2. **`redis` (Redis 7):** Distributed session cache and rate limiting database.
3. **`backend` (FastAPI):** Application layer running outbound HTTP providers.
4. **`frontend` (Next.js 14):** Administrator dashboard.

## Database Migrations

Alembic is utilized for managing PostgreSQL schemas. The configuration is stored at `backend/alembic.ini`.

### Initialize / Apply Migrations
To run migrations inside your local workspace or container:
```bash
# Run from backend directory
alembic upgrade head
```

### Creating new Migrations
Whenever you edit SQLAlchemy database models, generate a new auto-detected migration script:
```bash
# Run from backend directory
alembic revision --autogenerate -m "describe_database_changes"
```
