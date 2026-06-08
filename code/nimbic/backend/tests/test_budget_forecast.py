import uuid
import csv
import io
import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient
from sqlalchemy import select

from app.models.org import Organization, OrganizationPlan
from app.models.finops import Team, Project, Budget, BudgetAlert, BudgetScopeEnum, BudgetPeriodEnum
from app.models.request_log import RequestLog, ProviderEnum
from app.finops import budget_service, forecast_service
from app.main import app
from app.db.session import get_db


@pytest.mark.asyncio
async def test_budget_service_get_spend_and_set_budget(db):
    # Setup dummy organization
    org = Organization(name="Test Org Budget", slug="test-org-budget", plan=OrganizationPlan.ENTERPRISE)
    db.add(org)
    await db.commit()
    await db.refresh(org)

    # Set budget
    budget = await budget_service.set_budget(
        org_id=org.id,
        scope_type="org",
        scope_id=org.id,
        limit_usd=Decimal("150.00"),
        period="monthly",
        alert_pct=80,
        hard_limit=True,
        db=db
    )
    assert budget.limit_usd == Decimal("150.00")
    assert budget.hard_limit is True

    # Seed RequestLog rows for spend
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    log = RequestLog(
        org_id=org.id,
        request_id="req_spend_1",
        provider=ProviderEnum.openai,
        model="gpt-4o",
        prompt_tokens=1000,
        completion_tokens=500,
        cost_usd=Decimal("30.00"),
        latency_ms=100,
        status_code=200,
        created_at=now
    )
    db.add(log)
    await db.commit()

    # Get spend summary
    summary = await budget_service.get_spend(org.id, "org", org.id, "monthly", db)
    assert summary["total_cost_usd"] == Decimal("30.00")
    assert summary["total_requests"] == 1
    assert summary["budget_limit_usd"] == Decimal("150.00")
    assert summary["usage_pct"] == 20.0
    assert len(summary["daily_breakdown"]) == 1
    assert len(summary["model_breakdown"]) == 1
    assert summary["trend"] == "stable"


@pytest.mark.asyncio
async def test_budget_service_get_all_budgets_status(db):
    org = Organization(name="Test Org All Budgets", slug="test-org-all-budgets", plan=OrganizationPlan.ENTERPRISE)
    db.add(org)
    await db.commit()
    await db.refresh(org)

    # Create team
    team = Team(org_id=org.id, name="Biz Dev")
    db.add(team)
    await db.commit()
    await db.refresh(team)

    # Set budget under limit
    await budget_service.set_budget(
        org_id=org.id,
        scope_type="team",
        scope_id=team.id,
        limit_usd=Decimal("10.00"),
        period="daily",
        alert_pct=80,
        hard_limit=True,
        db=db
    )

    budgets = await budget_service.get_all_budgets(org.id, db)
    assert len(budgets) == 1
    assert budgets[0]["scope_name"] == "Biz Dev"
    assert budgets[0]["status"] == "ok"


@pytest.mark.asyncio
async def test_forecast_service_spend_models(db):
    org = Organization(name="Test Org Forecast", slug="test-org-forecast", plan=OrganizationPlan.ENTERPRISE)
    db.add(org)
    await db.commit()
    await db.refresh(org)

    # Insert 5 days of daily spend history
    now = datetime.now(timezone.utc)
    for i in range(5):
        log = RequestLog(
            org_id=org.id,
            request_id=f"req_f_{i}",
            provider=ProviderEnum.openai,
            model="gpt-4o",
            prompt_tokens=1000,
            completion_tokens=500,
            cost_usd=Decimal("10.00"),
            latency_ms=100,
            status_code=200,
            created_at=now - timedelta(days=i)
        )
        db.add(log)
    await db.commit()

    # Forecast
    forecast = await forecast_service.forecast_spend(org.id, "org", org.id, db)
    assert forecast["current_month_actual_usd"] == Decimal("50.00")
    assert forecast["forecast_confidence"] == 0.40  # Under 7 days
    assert forecast["model"] == "7day_avg"


@pytest.mark.asyncio
async def test_forecast_model_comparisons(db):
    org = Organization(name="Test Org Comp", slug="test-org-comp", plan=OrganizationPlan.ENTERPRISE)
    db.add(org)
    await db.commit()
    await db.refresh(org)

    # Insert logs
    log = RequestLog(
        org_id=org.id,
        request_id="req_comp_1",
        provider=ProviderEnum.openai,
        model="gpt-4",
        prompt_tokens=5000,  # complex
        completion_tokens=2000,
        cost_usd=Decimal("0.50"),
        latency_ms=100,
        status_code=200,
        created_at=datetime.now(timezone.utc)
    )
    db.add(log)
    await db.commit()

    comparison = await forecast_service.get_model_comparison(org.id, 30, db)
    assert len(comparison["current_mix"]) == 1
    assert len(comparison["scenarios"]) == 4

    scenarios = {s["name"]: s for s in comparison["scenarios"]}
    assert "Current mix" in scenarios
    assert "Optimised routing" in scenarios
    assert "All on cheapest model" in scenarios


@pytest.mark.asyncio
async def test_finops_router_teams_crud(client: AsyncClient, db):
    async def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db

    try:
        # Set up Organization and Admin key
        org = Organization(name="CRUD Org", slug="crud-org", plan=OrganizationPlan.ENTERPRISE)
        db.add(org)
        await db.commit()
        await db.refresh(org)

        from app.services import key_svc
        db_key, raw_key = await key_svc.create_api_key(
            org_id=org.id,
            name="Admin Key",
            scopes=["admin"],
            db=db
        )

        headers = {"Authorization": f"Bearer {raw_key}"}

        # 1. Create Team
        payload = {
            "name": "QA Team",
            "department": "Engineering",
            "budget_limit_usd": 100.0,
            "budget_alert_pct": 75
        }
        resp = await client.post("/api/v1/finops/teams", json=payload, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "QA Team"
        team_id = data["id"]

        # 2. List Teams
        list_resp = await client.get("/api/v1/finops/teams", headers=headers)
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1

        # 3. Patch Team
        patch_payload = {"name": "Quality Assurance"}
        patch_resp = await client.patch(f"/api/v1/finops/teams/{team_id}", json=patch_payload, headers=headers)
        assert patch_resp.status_code == 200
        assert patch_resp.json()["name"] == "Quality Assurance"

        # 4. Delete Team
        del_resp = await client.delete(f"/api/v1/finops/teams/{team_id}", headers=headers)
        assert del_resp.status_code == 200
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_finops_router_chargeback(client: AsyncClient, db):
    async def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db

    try:
        # Set up Organization and Admin key
        org = Organization(name="Chargeback Org", slug="chargeback-org", plan=OrganizationPlan.ENTERPRISE)
        db.add(org)
        await db.commit()
        await db.refresh(org)

        from app.services import key_svc
        db_key, raw_key = await key_svc.create_api_key(
            org_id=org.id,
            name="Admin Key",
            scopes=["admin"],
            db=db
        )

        headers = {"Authorization": f"Bearer {raw_key}"}

        # Seed RequestLog row
        log = RequestLog(
            org_id=org.id,
            request_id="req_charge_1",
            provider=ProviderEnum.openai,
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=Decimal("0.05"),
            latency_ms=100,
            status_code=200,
            department="Finance",
            created_at=datetime.now(timezone.utc)
        )
        db.add(log)
        await db.commit()

        # Get JSON chargeback report
        current_month_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")[:7]  # YYYY-MM
        resp = await client.get(
            f"/api/v1/finops/chargeback?period={current_month_str}&group_by=department",
            headers=headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["group_by"] == "department"
        assert float(data["total_cost_usd"]) == 0.05
        assert len(data["items"]) == 1
        assert data["items"][0]["group_key"] == "Finance"
        assert data["items"][0]["percentage"] == 100.0

        # Get CSV chargeback download
        csv_resp = await client.get(
            f"/api/v1/finops/chargeback/download?period={current_month_str}&group_by=department",
            headers=headers
        )
        assert csv_resp.status_code == 200
        assert "text/csv" in csv_resp.headers["content-type"]
        assert "attachment" in csv_resp.headers["content-disposition"]
    finally:
        app.dependency_overrides.clear()
