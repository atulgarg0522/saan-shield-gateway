import uuid
import pytest
import csv
import io
import asyncio
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient
from sqlalchemy import select

from app.models.org import Organization, OrganizationPlan
from app.models.finops import Team, Project, Budget, BudgetAlert, BudgetScopeEnum, BudgetPeriodEnum, BudgetAlertTypeEnum
from app.models.request_log import RequestLog, ProviderEnum
from app.services import org_svc, key_svc
from app.services.proxy_svc import ProxyResult
from app.routing.prompt_classifier import PromptClassification
from app.routing.smart_router import RouteDecision
from app.finops import budget_service, forecast_service
from app.main import app
from app.db.session import get_db

@pytest.fixture(autouse=True)
def clean_dependency_overrides():
    yield
    app.dependency_overrides.clear()

@pytest.fixture(autouse=True)
def mock_external_calls():
    # Mock classifier to avoid HuggingFace model downloads
    mock_classification = PromptClassification(
        complexity="simple",
        category="chat",
        estimated_tokens=10,
        recommended_model="gpt-4o",
        recommended_provider="openai",
        confidence=0.9,
        reasoning="mocked reasoning"
    )
    
    mock_decision = RouteDecision(
        model="gpt-4o",
        provider=ProviderEnum.openai,
        routing_reason="mocked route",
        fallback_chain=[],
        estimated_cost_usd=Decimal("0.01"),
        baseline_cost_usd=Decimal("0.01")
    )

    mock_res = ProxyResult(
        response_body={"choices": [{"message": {"content": "mock content"}}]},
        prompt_tokens=10,
        completion_tokens=5,
        cost_usd=Decimal("0.01"),
        status_code=200,
        actual_provider="openai",
        actual_model="gpt-4o"
    )

    patcher_classify = patch("app.routing.prompt_classifier.PromptClassifier.classify", AsyncMock(return_value=mock_classification))
    patcher_route = patch("app.routing.smart_router.SmartRouter.route", AsyncMock(return_value=mock_decision))
    patcher_call = patch("app.services.proxy_svc.call_provider", AsyncMock(return_value=mock_res))

    patcher_classify.start()
    patcher_route.start()
    patcher_call.start()

    yield

    patcher_classify.stop()
    patcher_route.stop()
    patcher_call.stop()

async def get_test_headers(org_id, db):
    _, raw_key = await key_svc.create_api_key(
        org_id=org_id,
        name="Test Proxy Key",
        scopes=["proxy", "admin"],
        db=db
    )
    return {"Authorization": f"Bearer {raw_key}"}

# ==========================================
# TEST ATTRIBUTION
# ==========================================

@pytest.mark.asyncio
async def test_team_auto_created(client: AsyncClient, db):
    async def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db

    org = await org_svc.create_org("Attr Org 1", "attr-org-1", OrganizationPlan.ENTERPRISE, db)
    headers = await get_test_headers(org.id, db)
    headers["X-SaaN-Team"] = "newteam"
    headers["X-SaaN-Department"] = "RnD"

    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello!"}]
    }

    resp = await client.post("/v1/chat/completions", json=payload, headers=headers)
    assert resp.status_code == 200

    # Wait briefly for background tasks to commit
    await asyncio.sleep(0.1)

    stmt = select(Team).where(Team.name == "newteam")
    res = await db.execute(stmt)
    team = res.scalar_one_or_none()
    assert team is not None
    assert team.department == "RnD"


@pytest.mark.asyncio
async def test_project_attributed(client: AsyncClient, db):
    async def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db

    org = await org_svc.create_org("Attr Org 2", "attr-org-2", OrganizationPlan.ENTERPRISE, db)
    headers = await get_test_headers(org.id, db)
    headers["X-SaaN-Team"] = "Engineering"
    headers["X-SaaN-Project"] = "AI_Assistant"

    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello!"}]
    }

    resp = await client.post("/v1/chat/completions", json=payload, headers=headers)
    assert resp.status_code == 200

    # Wait briefly for background tasks to execute
    await asyncio.sleep(0.1)

    stmt_p = select(Project).where(Project.name == "AI_Assistant")
    res_p = await db.execute(stmt_p)
    project = res_p.scalar_one_or_none()
    assert project is not None
    
    stmt_log = select(RequestLog).where(RequestLog.project_id == project.id)
    res_log = await db.execute(stmt_log)
    log = res_log.scalar_one_or_none()
    assert log is not None


@pytest.mark.asyncio
async def test_unknown_tag_handled(client: AsyncClient, db):
    async def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db

    org = await org_svc.create_org("Attr Org 3", "attr-org-3", OrganizationPlan.ENTERPRISE, db)
    headers = await get_test_headers(org.id, db)
    headers["X-SaaN-Team"] = ""

    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello!"}]
    }

    resp = await client.post("/v1/chat/completions", json=payload, headers=headers)
    assert resp.status_code == 200

    # Wait briefly for background tasks
    await asyncio.sleep(0.1)

    stmt = select(RequestLog).where(RequestLog.org_id == org.id)
    logs = (await db.execute(stmt)).scalars().all()
    assert len(logs) == 1
    assert logs[0].team_id is None


# ==========================================
# TEST BUDGETS
# ==========================================

@pytest.mark.asyncio
async def test_soft_limit(client: AsyncClient, db):
    async def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db

    org = await org_svc.create_org("Budget Org 1", "budget-org-1", OrganizationPlan.ENTERPRISE, db)
    headers = await get_test_headers(org.id, db)
    headers["X-SaaN-Team"] = "Sales"

    team = Team(org_id=org.id, name="Sales")
    db.add(team)
    await db.commit()
    await db.refresh(team)

    await budget_service.set_budget(
        org_id=org.id,
        scope_type="team",
        scope_id=team.id,
        limit_usd=Decimal("10.00"),
        period="monthly",
        alert_pct=80,
        hard_limit=False,
        db=db
    )

    # Seed spend at 85% ($8.50) with latency_ms and total_tokens set
    log_spend = RequestLog(
        org_id=org.id,
        team_id=team.id,
        request_id="soft_1",
        provider=ProviderEnum.openai,
        model="gpt-4o",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cost_usd=Decimal("8.50"),
        latency_ms=100,
        status_code=200,
        created_at=datetime.now(timezone.utc)
    )
    db.add(log_spend)
    await db.commit()

    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello!"}]
    }

    resp = await client.post("/v1/chat/completions", json=payload, headers=headers)
    assert resp.status_code == 200

    # Wait briefly for background tasks
    await asyncio.sleep(0.1)

    stmt = select(BudgetAlert).where(BudgetAlert.org_id == org.id)
    alerts = (await db.execute(stmt)).scalars().all()
    assert len(alerts) >= 1
    assert alerts[0].usage_pct >= Decimal("85.00")
    assert alerts[0].alert_type == "soft_warning"


@pytest.mark.asyncio
async def test_hard_limit(client: AsyncClient, db):
    async def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db

    org = await org_svc.create_org("Budget Org 2", "budget-org-2", OrganizationPlan.ENTERPRISE, db)
    headers = await get_test_headers(org.id, db)
    headers["X-SaaN-Team"] = "Marketing"

    team = Team(org_id=org.id, name="Marketing")
    db.add(team)
    await db.commit()
    await db.refresh(team)

    await budget_service.set_budget(
        org_id=org.id,
        scope_type="team",
        scope_id=team.id,
        limit_usd=Decimal("10.00"),
        period="monthly",
        alert_pct=80,
        hard_limit=True,
        db=db
    )

    # Seed spend at 101% ($10.10) with latency_ms and total_tokens set
    log_spend = RequestLog(
        org_id=org.id,
        team_id=team.id,
        request_id="hard_1",
        provider=ProviderEnum.openai,
        model="gpt-4o",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cost_usd=Decimal("10.10"),
        latency_ms=100,
        status_code=200,
        created_at=datetime.now(timezone.utc)
    )
    db.add(log_spend)
    await db.commit()

    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello!"}]
    }

    resp = await client.post("/v1/chat/completions", json=payload, headers=headers)
    assert resp.status_code == 429
    assert "exceeded" in resp.json()["reason"]


@pytest.mark.asyncio
async def test_budget_reset(client: AsyncClient, db):
    async def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db

    org = await org_svc.create_org("Budget Org 3", "budget-org-3", OrganizationPlan.ENTERPRISE, db)
    headers = await get_test_headers(org.id, db)
    headers["X-SaaN-Team"] = "Support"

    team = Team(org_id=org.id, name="Support")
    db.add(team)
    await db.commit()
    await db.refresh(team)

    await budget_service.set_budget(
        org_id=org.id,
        scope_type="team",
        scope_id=team.id,
        limit_usd=Decimal("10.00"),
        period="monthly",
        alert_pct=80,
        hard_limit=True,
        db=db
    )

    # Seed spend in previous month costing $15.00 with latency_ms and total_tokens set
    past_time = datetime.now(timezone.utc) - timedelta(days=40)
    log_spend = RequestLog(
        org_id=org.id,
        team_id=team.id,
        request_id="past_1",
        provider=ProviderEnum.openai,
        model="gpt-4o",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cost_usd=Decimal("15.00"),
        latency_ms=100,
        status_code=200,
        created_at=past_time
    )
    db.add(log_spend)
    await db.commit()

    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello!"}]
    }

    resp = await client.post("/v1/chat/completions", json=payload, headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_multi_scope(client: AsyncClient, db):
    async def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db

    org = await org_svc.create_org("Budget Org 4", "budget-org-4", OrganizationPlan.ENTERPRISE, db)
    headers = await get_test_headers(org.id, db)
    headers["X-SaaN-Team"] = "Engineering"
    headers["X-SaaN-Project"] = "AI_Code"

    team = Team(org_id=org.id, name="Engineering")
    db.add(team)
    await db.commit()
    await db.refresh(team)

    project = Project(org_id=org.id, team_id=team.id, name="AI_Code")
    db.add(project)
    await db.commit()
    await db.refresh(project)

    await budget_service.set_budget(
        org_id=org.id,
        scope_type="team",
        scope_id=team.id,
        limit_usd=Decimal("100.00"),
        period="monthly",
        alert_pct=80,
        hard_limit=True,
        db=db
    )

    await budget_service.set_budget(
        org_id=org.id,
        scope_type="project",
        scope_id=project.id,
        limit_usd=Decimal("5.00"),
        period="monthly",
        alert_pct=80,
        hard_limit=True,
        db=db
    )

    # Seed project spend to $5.50 with latency_ms and total_tokens set
    log_spend = RequestLog(
        org_id=org.id,
        team_id=team.id,
        project_id=project.id,
        request_id="proj_exceed",
        provider=ProviderEnum.openai,
        model="gpt-4o",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cost_usd=Decimal("5.50"),
        latency_ms=100,
        status_code=200,
        created_at=datetime.now(timezone.utc)
    )
    db.add(log_spend)
    await db.commit()

    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello!"}]
    }

    resp = await client.post("/v1/chat/completions", json=payload, headers=headers)
    assert resp.status_code == 429


# ==========================================
# TEST FORECAST
# ==========================================

@pytest.mark.asyncio
async def test_linear_forecast(db):
    org = Organization(name="Forecast Org 1", slug="forecast-org-1", plan=OrganizationPlan.ENTERPRISE)
    db.add(org)
    await db.commit()
    await db.refresh(org)

    # Seed 30 days of data at exactly $10 per day with latency_ms and total_tokens set
    now = datetime.now(timezone.utc)
    for i in range(30):
        log = RequestLog(
            org_id=org.id,
            request_id=f"f_lin_{i}",
            provider=ProviderEnum.openai,
            model="gpt-4o",
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
            cost_usd=Decimal("10.00"),
            latency_ms=100,
            status_code=200,
            created_at=now - timedelta(days=i)
        )
        db.add(log)
    await db.commit()

    forecast = await forecast_service.forecast_spend(org.id, "org", org.id, db)
    assert abs(Number(forecast["current_month_forecast_usd"]) - 300.0) <= 300.0 * 0.15


@pytest.mark.asyncio
async def test_low_data_forecast(db):
    org = Organization(name="Forecast Org 2", slug="forecast-org-2", plan=OrganizationPlan.ENTERPRISE)
    db.add(org)
    await db.commit()
    await db.refresh(org)

    # Seed only 5 days of data with latency_ms and total_tokens set
    now = datetime.now(timezone.utc)
    for i in range(5):
        log = RequestLog(
            org_id=org.id,
            request_id=f"f_low_{i}",
            provider=ProviderEnum.openai,
            model="gpt-4o",
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
            cost_usd=Decimal("10.00"),
            latency_ms=100,
            status_code=200,
            created_at=now - timedelta(days=i)
        )
        db.add(log)
    await db.commit()

    forecast = await forecast_service.forecast_spend(org.id, "org", org.id, db)
    assert forecast["forecast_confidence"] < 0.50


@pytest.mark.asyncio
async def test_accelerating_spend(db):
    org = Organization(name="Forecast Org 3", slug="forecast-org-3", plan=OrganizationPlan.ENTERPRISE)
    db.add(org)
    await db.commit()
    await db.refresh(org)

    # Seed 30 days of data: first 23 days at $10/day, last 7 days at $20/day with latency_ms and total_tokens set
    now = datetime.now(timezone.utc)
    for i in range(30):
        daily_cost = Decimal("20.00") if i < 7 else Decimal("10.00")
        log = RequestLog(
            org_id=org.id,
            request_id=f"f_acc_{i}",
            provider=ProviderEnum.openai,
            model="gpt-4o",
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
            cost_usd=daily_cost,
            latency_ms=100,
            status_code=200,
            created_at=now - timedelta(days=i)
        )
        db.add(log)
    await db.commit()

    forecast = await forecast_service.forecast_spend(org.id, "org", org.id, db)
    assert forecast["model"] == "7day_avg"
    assert forecast["daily_avg_usd"] == Decimal("20.00")


# ==========================================
# TEST CHARGEBACK
# ==========================================

@pytest.mark.asyncio
async def test_csv_export(client: AsyncClient, db):
    async def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db

    org = await org_svc.create_org("Cb Org 1", "cb-org-1", OrganizationPlan.ENTERPRISE, db)
    headers = await get_test_headers(org.id, db)

    # Seed logs with latency_ms and total_tokens set
    log = RequestLog(
        org_id=org.id,
        request_id="cb_csv_1",
        provider=ProviderEnum.openai,
        model="gpt-4o",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cost_usd=Decimal("1.50"),
        latency_ms=100,
        status_code=200,
        department="Engineering",
        created_at=datetime.now(timezone.utc)
    )
    db.add(log)
    await db.commit()

    current_month_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")[:7]
    resp = await client.get(
        f"/api/v1/finops/chargeback/download?period={current_month_str}&group_by=department",
        headers=headers
    )
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    
    content = resp.content.decode("utf-8")
    csv_file = io.StringIO(content)
    reader = csv.reader(csv_file)
    rows = list(reader)
    
    assert any("Engineering" in r for r in rows)
    assert any("1.500000" in r for r in rows)


@pytest.mark.asyncio
async def test_groupby_team(client: AsyncClient, db):
    async def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db

    org = await org_svc.create_org("Cb Org 2", "cb-org-2", OrganizationPlan.ENTERPRISE, db)
    headers = await get_test_headers(org.id, db)

    team_a = Team(org_id=org.id, name="TeamAlpha")
    team_b = Team(org_id=org.id, name="TeamBeta")
    db.add_all([team_a, team_b])
    await db.commit()
    await db.refresh(team_a)
    await db.refresh(team_b)

    log_a = RequestLog(
        org_id=org.id,
        team_id=team_a.id,
        request_id="cb_team_a",
        provider=ProviderEnum.openai,
        model="gpt-4o",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cost_usd=Decimal("2.00"),
        latency_ms=100,
        status_code=200,
        created_at=datetime.now(timezone.utc)
    )
    log_b = RequestLog(
        org_id=org.id,
        team_id=team_b.id,
        request_id="cb_team_b",
        provider=ProviderEnum.openai,
        model="gpt-4o",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cost_usd=Decimal("3.00"),
        latency_ms=100,
        status_code=200,
        created_at=datetime.now(timezone.utc)
    )
    db.add_all([log_a, log_b])
    await db.commit()

    current_month_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")[:7]
    resp = await client.get(
        f"/api/v1/finops/chargeback?period={current_month_str}&group_by=team",
        headers=headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    groups = {item["group_key"]: float(item["total_cost_usd"]) for item in data["items"]}
    assert groups["TeamAlpha"] == 2.0
    assert groups["TeamBeta"] == 3.0


@pytest.mark.asyncio
async def test_groupby_department(client: AsyncClient, db):
    async def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db

    org = await org_svc.create_org("Cb Org 3", "cb-org-3", OrganizationPlan.ENTERPRISE, db)
    headers = await get_test_headers(org.id, db)

    log_a = RequestLog(
        org_id=org.id,
        request_id="cb_dept_a",
        provider=ProviderEnum.openai,
        model="gpt-4o",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cost_usd=Decimal("4.00"),
        latency_ms=100,
        status_code=200,
        department="Engineering",
        created_at=datetime.now(timezone.utc)
    )
    log_b = RequestLog(
        org_id=org.id,
        request_id="cb_dept_b",
        provider=ProviderEnum.openai,
        model="gpt-4o",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cost_usd=Decimal("6.00"),
        latency_ms=100,
        status_code=200,
        department="Engineering",
        created_at=datetime.now(timezone.utc)
    )
    db.add_all([log_a, log_b])
    await db.commit()

    current_month_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")[:7]
    resp = await client.get(
        f"/api/v1/finops/chargeback?period={current_month_str}&group_by=department",
        headers=headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["group_key"] == "Engineering"
    assert float(data["items"][0]["total_cost_usd"]) == 10.0


# Helper functions
def Number(val):
    if val is None:
        return 0.0
    return float(val)
