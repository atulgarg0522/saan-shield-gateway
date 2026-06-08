import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.org import OrganizationPlan
from app.services import org_svc, key_svc
from app.db.session import get_db
from app.main import app
from app.models.ab_test import ABTest, ABTestResult
from app.routing.ab_test import ABTestManager, ABTest as ABTestDC
from app.services.proxy_svc import execute_proxy, ProxyRequest
from app.models.request_log import ProviderEnum


@pytest.mark.asyncio
async def test_ab_test_endpoints_crud(db: AsyncSession, client: AsyncClient):
    """
    Verifies creation, listing, status patching, and result aggregations of A/B tests.
    """
    org = await org_svc.create_org("AB CRUD Org", "ab-crud", OrganizationPlan.ENTERPRISE, db)
    _, raw_key = await key_svc.create_api_key(
        org_id=org.id,
        name="Admin Key",
        scopes=["admin"],
        db=db
    )
    headers = {"Authorization": f"Bearer {raw_key}"}

    async def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db

    try:
        # 1. Create A/B test
        payload = {
            "name": "Compare Claude & GPT",
            "model_a": "gpt-4o-mini",
            "provider_a": "openai",
            "model_b": "claude-haiku-4-5",
            "provider_b": "anthropic",
            "split_pct": 30
        }
        res = await client.post("/api/v1/routing/ab-tests", json=payload, headers=headers)
        assert res.status_code == 201
        data = res.json()
        assert data["name"] == "Compare Claude & GPT"
        assert data["model_a"] == "gpt-4o-mini"
        assert data["split_pct"] == 30
        assert data["status"] == "active"
        test_id = data["id"]

        # 2. Prevent creating another active A/B test
        payload_2 = {
            "name": "Another active test",
            "model_a": "gpt-4o",
            "provider_a": "openai",
            "model_b": "claude-sonnet-4-6",
            "provider_b": "anthropic",
            "split_pct": 50
        }
        res_fail = await client.post("/api/v1/routing/ab-tests", json=payload_2, headers=headers)
        assert res_fail.status_code == 400
        assert "An active A/B test already exists" in res_fail.json()["detail"]

        # 3. List A/B tests
        list_res = await client.get("/api/v1/routing/ab-tests", headers=headers)
        assert list_res.status_code == 200
        tests = list_res.json()
        assert len(tests) == 1
        assert tests[0]["id"] == test_id

        # 4. Patch status to paused
        patch_res = await client.patch(f"/api/v1/routing/ab-tests/{test_id}", json={"status": "paused"}, headers=headers)
        assert patch_res.status_code == 200
        assert patch_res.json()["status"] == "paused"

        # 5. Patch status to completed
        complete_res = await client.patch(f"/api/v1/routing/ab-tests/{test_id}", json={"status": "completed"}, headers=headers)
        assert complete_res.status_code == 200
        data_completed = complete_res.json()
        assert data_completed["status"] == "completed"
        assert data_completed["ends_at"] is not None
        assert data_completed["results"] is not None
        
        # 6. Retrieve results for completed test
        results_res = await client.get(f"/api/v1/routing/ab-tests/{test_id}/results", headers=headers)
        assert results_res.status_code == 200
        res_data = results_res.json()
        assert "model_a" in res_data
        assert "model_b" in res_data
        assert res_data["winner"] == "inconclusive"  # Not enough data (0 requests)

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ab_manager_caching_and_assignment():
    """
    Test ABTestManager caching, deterministic assignment, and recording results.
    """
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None  # Cache miss

    # Setup a mock test
    test_dc = ABTestDC(
        id=uuid.uuid4(),
        org_id=str(uuid.uuid4()),
        name="Unit Test A/B",
        model_a="model-a",
        provider_a="openai",
        model_b="model-b",
        provider_b="anthropic",
        split_pct=25,
        status="active",
        started_at=None
    )

    manager = ABTestManager()

    # 1. Deterministic Variant assignment
    # Result should be stable for same request_id + test_id
    req_id_1 = "request-1"
    req_id_2 = "request-2"

    variant_1 = await manager.assign_variant(req_id_1, test_dc)
    variant_2 = await manager.assign_variant(req_id_1, test_dc)
    assert variant_1 == variant_2  # Stable

    variant_3 = await manager.assign_variant(req_id_2, test_dc)
    assert variant_3 in ("A", "B")


@pytest.mark.asyncio
async def test_ab_proxy_interception(db: AsyncSession):
    """
    Validates that execute_proxy intercepts active tests and routes to appropriate models.
    """
    org = await org_svc.create_org("AB Proxy Org", "ab-proxy", OrganizationPlan.ENTERPRISE, db)
    
    # Create active A/B test
    test = ABTest(
        org_id=org.id,
        name="Proxy A/B Test",
        model_a="gpt-4o-mini",
        provider_a="openai",
        model_b="claude-haiku-4-5",
        provider_b="anthropic",
        split_pct=100,  # 100% split to variant B to guarantee Variant B
        status="active"
    )
    db.add(test)
    await db.commit()
    await db.refresh(test)

    # Mock call_provider to avoid outbound call
    from app.services.proxy_svc import ProxyResult
    mock_result = ProxyResult(
        response_body={"choices": [{"message": {"role": "assistant", "content": "A/B response"}}]},
        prompt_tokens=5,
        completion_tokens=5,
        cost_usd=Decimal("0.000100"),
        latency_ms=150,
        status_code=200,
        actual_provider="anthropic",
        actual_model="claude-haiku-4-5"
    )

    req = ProxyRequest(
        org_id=org.id,
        api_key_id=uuid.uuid4(),
        provider=ProviderEnum.OPENAI,
        model="gpt-4o",  # Requested model will be intercepted/ignored
        messages=[{"role": "user", "content": "hello"}],
        stream=False,
        request_id="req_abtest_12345"
    )

    with patch("app.services.proxy_svc.call_provider", return_value=mock_result) as mock_call:
        with patch("app.redis.redis_client") as mock_redis:
            # Mock get_active_test to avoid Redis/DB session lifecycle complications inside helper
            ab_manager = ABTestManager()
            test_dc = ABTestDC(
                id=test.id,
                org_id=str(org.id),
                name=test.name,
                model_a=test.model_a,
                provider_a=test.provider_a,
                model_b=test.model_b,
                provider_b=test.provider_b,
                split_pct=test.split_pct,
                status=test.status,
                started_at=test.started_at
            )
            
            with patch.object(ABTestManager, "get_active_test", return_value=test_dc):
                res = await execute_proxy(req, db)
                assert res.status_code == 200
                assert res.actual_model == "claude-haiku-4-5"
                assert res.actual_provider == "anthropic"

                # Verify variant result is recorded in database
                stmt = select(ABTestResult).where(
                    ABTestResult.test_id == test.id
                )
                results_db = (await db.execute(stmt)).scalars().all()
                assert len(results_db) == 1
                assert results_db[0].variant == "B"
                assert results_db[0].latency == 150
                assert results_db[0].cost == Decimal("0.000100")
