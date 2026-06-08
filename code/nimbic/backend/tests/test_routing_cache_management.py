import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.org import OrganizationPlan
from app.services import org_svc, key_svc
from app.db.session import get_db
from app.main import app
from app.models.routing_cache import RoutingRule, OrgFAQCache, PromptEmbedding, CostSavingsLog, CostSavingsSource
from app.models.request_log import RequestLog, ProviderEnum


@pytest.mark.asyncio
async def test_routing_rules_crud_and_reorder(db: AsyncSession, client: AsyncClient):
    """
    Verifies that routing rules can be created, listed, updated, reordered, and deleted.
    """
    org = await org_svc.create_org("Routing CRUD Org", "routing-crud", OrganizationPlan.FREE, db)
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
        # 1. Create a rule
        rule_payload = {
            "name": "Coding Override",
            "conditions": {"category": "coding"},
            "target_model": "gpt-4o-mini",
            "target_provider": "openai",
            "priority": 2
        }
        create_res = await client.post("/api/v1/routing/rules", json=rule_payload, headers=headers)
        assert create_res.status_code == 201
        rule_data = create_res.json()
        assert rule_data["name"] == "Coding Override"
        assert rule_data["priority"] == 2
        assert rule_data["target_model"] == "gpt-4o-mini"
        rule_id = rule_data["id"]

        # 2. Get rules list
        list_res = await client.get("/api/v1/routing/rules", headers=headers)
        assert list_res.status_code == 200
        rules = list_res.json()
        assert len(rules) == 1
        assert rules[0]["id"] == rule_id

        # 3. Update the rule
        update_payload = {
            "name": "Coding Override Updated",
            "priority": 1
        }
        patch_res = await client.patch(f"/api/v1/routing/rules/{rule_id}", json=update_payload, headers=headers)
        assert patch_res.status_code == 200
        updated_data = patch_res.json()
        assert updated_data["name"] == "Coding Override Updated"
        assert updated_data["priority"] == 1

        # 4. Create a second rule to test reordering
        second_payload = {
            "name": "Simple Translation Override",
            "conditions": {"complexity": "simple", "category": "translation"},
            "target_model": "claude-haiku-4-5",
            "target_provider": "anthropic",
            "priority": 5
        }
        create_res_2 = await client.post("/api/v1/routing/rules", json=second_payload, headers=headers)
        assert create_res_2.status_code == 201
        rule_2_id = create_res_2.json()["id"]

        # Reorder rule priorities
        reorder_payload = [
            {"id": rule_id, "priority": 10},
            {"id": rule_2_id, "priority": 3}
        ]
        reorder_res = await client.post("/api/v1/routing/rules/reorder", json=reorder_payload, headers=headers)
        assert reorder_res.status_code == 200

        # Check sorted order in GET
        list_res = await client.get("/api/v1/routing/rules", headers=headers)
        rules = list_res.json()
        assert len(rules) == 2
        # rule 2 (priority 3) should now be first, rule 1 (priority 10) second
        assert rules[0]["id"] == rule_2_id
        assert rules[1]["id"] == rule_id

        # 5. Delete rule
        del_res = await client.delete(f"/api/v1/routing/rules/{rule_id}", headers=headers)
        assert del_res.status_code == 204

        # Confirm deleted
        list_res = await client.get("/api/v1/routing/rules", headers=headers)
        rules = list_res.json()
        assert len(rules) == 1
        assert rules[0]["id"] == rule_2_id

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_routing_dry_run_simulation(db: AsyncSession, client: AsyncClient):
    """
    Asserts that the dry-run simulation classifies and route prompt correctly.
    """
    org = await org_svc.create_org("Routing Test Org", "routing-test-org", OrganizationPlan.FREE, db)
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
        # Mock semantic cache lookup queries inside /test
        with patch("app.routers.routing.redis_client") as mock_redis:
            # redis get returns None (cache miss)
            mock_redis.get = AsyncMock(return_value=None)
            
            payload = {"prompt": "Write a python function to merge two dictionaries."}
            res = await client.post("/api/v1/routing/test", json=payload, headers=headers)
            
            assert res.status_code == 200
            data = res.json()
            assert data["complexity"] == "medium"
            assert data["category"] == "coding"
            assert data["routed_provider"] == "anthropic"
            assert data["routed_model"] == "claude-sonnet-4-6"
            assert data["cache_would_hit"] is False
            assert float(data["baseline_cost_usd"]) > 0

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_routing_stats_aggregation(db: AsyncSession, client: AsyncClient):
    """
    Verifies that stats compiles and returns database aggregations correctly.
    """
    org = await org_svc.create_org("Routing Stats Org", "routing-stats-org", OrganizationPlan.FREE, db)
    _, raw_key = await key_svc.create_api_key(
        org_id=org.id,
        name="Admin Key",
        scopes=["admin"],
        db=db
    )
    headers = {"Authorization": f"Bearer {raw_key}"}

    # Add request logs
    log1 = RequestLog(
        org_id=org.id,
        request_id="req_stats_1",
        provider=ProviderEnum.openai,
        model="gpt-4o-mini",
        cost_usd=Decimal("0.000150"),
        latency_ms=120,
        status_code=200,
        request_metadata={"category": "chat", "routing_reason": "complexity default"}
    )
    log2 = RequestLog(
        org_id=org.id,
        request_id="req_stats_2",
        provider=ProviderEnum.anthropic,
        model="claude-sonnet-4-6",
        cost_usd=Decimal("0.003000"),
        latency_ms=450,
        status_code=200,
        request_metadata={"category": "coding", "routing_reason": "org rule match: Coding Rule"}
    )
    db.add_all([log1, log2])
    await db.commit()

    async def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db

    try:
        response = await client.get("/api/v1/routing/stats", headers=headers)
        assert response.status_code == 200
        data = response.json()
        
        # Verify model stats
        assert len(data["by_model"]) == 2
        models = [item["model"] for item in data["by_model"]]
        assert "gpt-4o-mini" in models
        assert "claude-sonnet-4-6" in models
        
        # Verify complexity stats
        complexities = {item["complexity"]: item["count"] for item in data["by_complexity"]}
        assert complexities["simple"] == 1  # mini
        assert complexities["medium"] == 1  # sonnet
        
        # Verify category stats
        categories = {item["category"]: item["count"] for item in data["by_category"]}
        assert categories["chat"] == 1
        assert categories["coding"] == 1
        
        # Verify rules triggered
        assert len(data["routing_rules_triggered"]) == 1
        assert data["routing_rules_triggered"][0]["rule_name"] == "Coding Rule"
        assert data["routing_rules_triggered"][0]["trigger_count"] == 1

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cache_stats_and_entries_flush(db: AsyncSession, client: AsyncClient):
    """
    Verifies that cache stats, paginated list of entries, deletion, and flushing work.
    """
    org = await org_svc.create_org("Cache Stats Org", "cache-stats-org", OrganizationPlan.FREE, db)
    _, raw_key = await key_svc.create_api_key(
        org_id=org.id,
        name="Admin Key",
        scopes=["admin"],
        db=db
    )
    headers = {"Authorization": f"Bearer {raw_key}"}

    # Add a prompt embedding cache entry
    embedding = PromptEmbedding(
        org_id=org.id,
        prompt_hash="xyz123hash",
        embedding=[0.1] * 384,
        response_text="cached response text",
        model_used="gpt-4o",
        hit_count=5
    )
    
    # Add a savings log entry
    savings = CostSavingsLog(
        org_id=org.id,
        request_id="req_savings_1",
        actual_model="gpt-4o-mini",
        actual_cost_usd=Decimal("0.000000"),
        baseline_model="gpt-4o",
        baseline_cost_usd=Decimal("0.005000"),
        savings_usd=Decimal("0.005000"),
        source=CostSavingsSource.cache_hit
    )
    db.add_all([embedding, savings])
    await db.commit()

    async def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db

    try:
        # 1. Fetch cache stats
        stats_res = await client.get("/api/v1/cache/stats", headers=headers)
        assert stats_res.status_code == 200
        stats = stats_res.json()
        assert stats["total_entries"] == 1
        assert float(stats["total_savings_usd"]) == 0.005000

        # 2. List cache entries
        entries_res = await client.get("/api/v1/cache/entries", headers=headers)
        assert entries_res.status_code == 200
        entries = entries_res.json()["items"]
        assert len(entries) == 1
        entry_id = entries[0]["id"]
        assert entries[0]["hit_count"] == 5
        assert entries[0]["model"] == "gpt-4o"
        assert "cached response text" in entries[0]["prompt_snippet"]

        # 3. Delete cache entry
        del_res = await client.delete(f"/api/v1/cache/entries/{entry_id}", headers=headers)
        assert del_res.status_code == 204

        # Confirm deleted
        entries_res = await client.get("/api/v1/cache/entries", headers=headers)
        assert len(entries_res.json()["items"]) == 0

        # Add another entry to test flush
        embedding_2 = PromptEmbedding(
            org_id=org.id,
            prompt_hash="abc456hash",
            embedding=[0.2] * 384,
            response_text="another response",
            model_used="claude-sonnet-4-6",
            hit_count=2
        )
        db.add(embedding_2)
        await db.commit()

        # 4. Flush cache (confirm=false yields 400 error)
        flush_res_fail = await client.post("/api/v1/cache/flush?confirm=false", headers=headers)
        assert flush_res_fail.status_code == 400

        # Confirm=true clears it successfully
        with patch("app.routers.cache.redis_client") as mock_redis:
            mock_redis.delete = AsyncMock()
            flush_res = await client.post("/api/v1/cache/flush?confirm=true", headers=headers)
            assert flush_res.status_code == 200
            
            # Confirm prompt embeddings table is empty
            entries_res = await client.get("/api/v1/cache/entries", headers=headers)
            assert len(entries_res.json()["items"]) == 0

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cache_faq_crud_and_bulk_csv(db: AsyncSession, client: AsyncClient):
    """
    Verifies FAQ CRUD endpoints and CSV bulk upload functionality.
    """
    org = await org_svc.create_org("FAQ Test Org", "faq-test-org", OrganizationPlan.FREE, db)
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
        # Mock SemanticCache get_embedding to return list of floats
        with patch("app.routing.semantic_cache.SemanticCache.get_embedding", AsyncMock(return_value=[0.5] * 384)):
            # 1. Create FAQ entry
            faq_payload = {
                "question": "What is the return policy?",
                "answer": "Returns are allowed within 30 days.",
                "category": "shipping"
            }
            create_res = await client.post("/api/v1/cache/faq", json=faq_payload, headers=headers)
            assert create_res.status_code == 201
            faq_data = create_res.json()
            assert faq_data["question"] == "What is the return policy?"
            assert faq_data["category"] == "shipping"
            faq_id = faq_data["id"]

            # 2. Get FAQ list
            list_res = await client.get("/api/v1/cache/faq", headers=headers)
            assert list_res.status_code == 200
            faqs = list_res.json()
            assert len(faqs) == 1
            assert faqs[0]["id"] == faq_id

            # 3. Update FAQ entry (triggers re-embed)
            update_payload = {
                "question": "What is the return policy updated?",
                "answer": "Returns are allowed within 15 days."
            }
            patch_res = await client.patch(f"/api/v1/cache/faq/{faq_id}", json=update_payload, headers=headers)
            assert patch_res.status_code == 200
            updated_data = patch_res.json()
            assert updated_data["question"] == "What is the return policy updated?"
            assert updated_data["answer"] == "Returns are allowed within 15 days."

            # 4. Delete FAQ entry
            del_res = await client.delete(f"/api/v1/cache/faq/{faq_id}", headers=headers)
            assert del_res.status_code == 204

            # Verify FAQ list empty
            list_res = await client.get("/api/v1/cache/faq", headers=headers)
            assert len(list_res.json()) == 0

            # 5. Bulk upload CSV
            csv_content = (
                "question,answer,category\n"
                "How do I sign up?,Click the signup button,onboarding\n"
                "Where is my invoice?,Go to Billing tab,billing\n"
                "Row with missing data,,onboarding\n"
            )
            files = {
                "file": ("faqs.csv", csv_content.encode("utf-8"), "text/csv")
            }
            bulk_res = await client.post("/api/v1/cache/faq/bulk", files=files, headers=headers)
            print("BULK RESPONSE:", bulk_res.status_code, bulk_res.text)
            assert bulk_res.status_code == 200
            bulk_data = bulk_res.json()
            assert bulk_data["imported"] == 2
            assert bulk_data["failed"] == 1
            assert len(bulk_data["errors"]) == 1
            assert "Row 4: Missing question or answer" in bulk_data["errors"][0]

            # Confirm 2 FAQ items imported
            list_res = await client.get("/api/v1/cache/faq", headers=headers)
            assert len(list_res.json()) == 2

    finally:
        app.dependency_overrides.clear()
