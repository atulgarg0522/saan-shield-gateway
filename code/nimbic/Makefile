# --- Makefile for SaaN Shield AI Gateway Orchestration ---

.PHONY: dev migrate seed seed-security seed-finops test logs down clean help

# Default command: display help instructions
help:
	@echo "Available commands:"
	@echo "  make dev       - Spin up all services in docker-compose with hot reload"
	@echo "  make migrate   - Execute Alembic database schema migrations to head"
	@echo "  make seed      - Populate database with Demo Org, Admin API key, and providers"
	@echo "  make seed-security - Seed security policy and violations for dashboard demo"
	@echo "  make seed-finops - Seed teams, projects, budgets, and spend alerts for FinOps demo"
	@echo "  make test      - Run Pytest suite inside backend service container"
	@echo "  make logs      - Tail log output streams from all active services"
	@echo "  make down      - Tear down active containers and clean networks"
	@echo "  make clean     - Tear down containers, networks, and purge database volumes"

dev:
	docker-compose up --build

migrate:
	docker-compose exec backend alembic upgrade head

seed:
	docker-compose exec backend python infra/seed.py

seed-security:
	docker-compose exec backend python infra/seed_security.py

seed-phase3:
	docker-compose exec backend python infra/seed_phase3.py

seed-finops:
	docker-compose exec backend python infra/seed_finops.py

test:
	docker-compose exec backend pytest

logs:
	docker-compose logs -f

down:
	docker-compose down

clean:
	docker-compose down -v
