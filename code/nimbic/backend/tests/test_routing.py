import json
import uuid
import pytest
import tiktoken
from unittest.mock import AsyncMock, patch
from decimal import Decimal
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.routing.prompt_classifier import PromptClassifier
from app.routing.semantic_cache import SemanticCache
from app.routing.smart_router import SmartRouter
from app.services import org_svc
from app.models.org import OrganizationPlan
from app.models.routing_cache import RoutingRule, OrgFAQCache, PromptEmbedding


# ==========================================
# PROMPT CLASSIFIER TESTS
# ==========================================

@pytest.mark.asyncio
async def test_simple_greeting():
    """
    Verifies that 'Hello, how are you?' classifies as a simple chat prompt.
    """
    classifier = PromptClassifier()
    res = await classifier.classify("Hello, how are you?")
    assert res.complexity == "simple"
    assert res.category == "chat"


@pytest.mark.asyncio
async def test_complex_analysis():
    """
    Verifies that a long/analytical prompt classifies as complex.
    """
    classifier = PromptClassifier()
    prompt = (
        "analyze in depth the following research paper and evaluate the methodology, "
        "concluding with potential flaws in the hypothesis testing. "
        "Here is the text to analyze in detail: ..."
    )
    res = await classifier.classify(prompt)
    assert res.complexity == "complex"
    assert res.category == "analysis"


@pytest.mark.asyncio
async def test_coding_medium():
    """
    Verifies coding category and medium complexity suggestion.
    """
    classifier = PromptClassifier()
    res = await classifier.classify("write a python function to parse JSON")
    assert res.category == "coding"
    assert res.complexity == "medium"


@pytest.mark.asyncio
async def test_token_estimate():
    """
    Verifies that estimated tokens count is within 10% of tiktoken.
    """
    classifier = PromptClassifier()
    prompt = "write a python function to parse JSON and retrieve records from a database"
    res = await classifier.classify(prompt)
    
    encoding = tiktoken.get_encoding("cl100k_base")
    actual_tokens = len(encoding.encode(prompt))
    
    assert abs(res.estimated_tokens - actual_tokens) <= actual_tokens * 0.1


@pytest.mark.asyncio
async def test_math_complex():
    """
    Verifies math prompts are categorized as math and complex.
    """
    classifier = PromptClassifier()
    res = await classifier.classify("solve the math equation x^2 = 4")
    assert res.category == "math"
    assert res.complexity == "complex"


# ==========================================
# SEMANTIC CACHE TESTS
# ==========================================

@pytest.mark.asyncio
async def test_exact_hit(db: AsyncSession):
    """
    Tests exact Redis cache hit layer.
    """
    org = await org_svc.create_org("Cache Org 1", "cache-org-1", OrganizationPlan.ENTERPRISE, db)
    redis_mock = AsyncMock()
    
    cache = SemanticCache()
    prompt = "what is the leave policy?"
    response = "HR leave policy is 20 days per year."
    
    # Store exact cache
    await cache.store(prompt, response, "gpt-4o", org.id, db, redis_mock)
    
    # Verify setex was called
    assert redis_mock.setex.called
    
    # Setup mock Redis response for lookup
    normalized = cache.normalize_prompt(prompt)
    h = cache.hash_prompt(normalized)
    redis_mock.get.return_value = json.dumps({"response": response, "model": "gpt-4o"})
    
    # Look up
    result = await cache.lookup(prompt, org.id, db, redis_mock)
    assert result.hit is True
    assert result.source == "exact"
    assert result.response == response


@pytest.mark.asyncio
async def test_semantic_hit(db: AsyncSession):
    """
    Tests vector pgvector semantic lookup hit.
    """
    org = await org_svc.create_org("Cache Org 2", "cache-org-2", OrganizationPlan.ENTERPRISE, db)
    redis_mock = AsyncMock()
    redis_mock.get.return_value = None  # Cache miss on Redis
    
    cache = SemanticCache()
    prompt_store = "what is machine learning"
    response = "Machine learning is a subset of artificial intelligence."
    prompt_query = "what is machine learning"
    
    # Store the first prompt
    await cache.store(prompt_store, response, "gpt-4o", org.id, db, redis_mock)
    
    # Verify lookup of semantic match
    result = await cache.lookup(prompt_query, org.id, db, redis_mock)
    assert result.hit is True
    assert result.source == "semantic"
    assert result.response == response


@pytest.mark.asyncio
async def test_semantic_miss(db: AsyncSession):
    """
    Tests cache miss for very different prompts.
    """
    org = await org_svc.create_org("Cache Org 3", "cache-org-3", OrganizationPlan.ENTERPRISE, db)
    redis_mock = AsyncMock()
    redis_mock.get.return_value = None
    
    cache = SemanticCache()
    await cache.store("how do I apply for leaves", "Send email to HR", "gpt-4o", org.id, db, redis_mock)
    
    # Look up completely different prompt
    # Since SentenceTransformer is loaded, the vectors are generated, but cosine similarity will be low.
    # To mock low similarity, we can temporarily patch cosine calculation or verify it returns miss
    # Let's verify with mock or real model (real model will return <0.80 similarity for these two prompts)
    result = await cache.lookup("what is the database migration strategy", org.id, db, redis_mock)
    assert result.hit is False


@pytest.mark.asyncio
async def test_faq_hit(db: AsyncSession):
    """
    Tests FAQ matching vector similarity layer.
    """
    org = await org_svc.create_org("Cache Org 4", "cache-org-4", OrganizationPlan.ENTERPRISE, db)
    redis_mock = AsyncMock()
    redis_mock.get.return_value = None
    
    cache = SemanticCache()
    faqs = [
        {"question": "How to reset corporate VPN", "answer": "Go to portal.vpn.com and click reset.", "category": "IT"}
      ]
    
    await cache.seed_faq(org.id, faqs, db)
    
    # Look up similar FAQ
    result = await cache.lookup("How to reset corporate VPN", org.id, db, redis_mock)
    assert result.hit is True
    assert result.source == "faq"
    assert result.response == faqs[0]["answer"]


@pytest.mark.asyncio
async def test_no_store_with_pii(db: AsyncSession):
    """
    Tests that cache is bypassed if PII violations flag is True.
    """
    org = await org_svc.create_org("Cache Org 5", "cache-org-5", OrganizationPlan.ENTERPRISE, db)
    redis_mock = AsyncMock()
    
    cache = SemanticCache()
    await cache.store("my phone is 12345", "Ok received", "gpt-4o", org.id, db, redis_mock, has_pii=True)
    
    # Verify not stored in Redis
    assert not redis_mock.setex.called
    
    # Verify not stored in db
    stmt = select(PromptEmbedding).where(PromptEmbedding.org_id == org.id)
    embeddings = (await db.execute(stmt)).scalars().all()
    assert len(embeddings) == 0


# ==========================================
# SMART ROUTER TESTS
# ==========================================

@pytest.mark.asyncio
async def test_simple_routes_to_haiku(db: AsyncSession):
    """
    Verifies that simple prompt routes to haiku or flash.
    """
    org = await org_svc.create_org("Router Org 1", "router-org-1", OrganizationPlan.ENTERPRISE, db)
    redis_mock = AsyncMock()
    redis_mock.get.return_value = None
    
    from app.routing.prompt_classifier import PromptClassification
    classification = PromptClassification(
        complexity="simple",
        category="chat",
        estimated_tokens=10,
        recommended_model="claude-haiku-4-5",
        recommended_provider="anthropic",
        confidence=0.9,
        reasoning="casual prompt"
    )
    
    router = SmartRouter()
    decision = await router.route(classification, org.id, db, redis_mock)
    assert decision.model in ("claude-haiku-4-5", "gpt-4o-mini", "gemini-flash") or "haiku" in decision.model or "flash" in decision.model


@pytest.mark.asyncio
async def test_complex_routes_to_opus(db: AsyncSession):
    """
    Verifies that complex prompt routes to opus or gpt-4o.
    """
    org = await org_svc.create_org("Router Org 2", "router-org-2", OrganizationPlan.ENTERPRISE, db)
    redis_mock = AsyncMock()
    redis_mock.get.return_value = None
    
    from app.routing.prompt_classifier import PromptClassification
    classification = PromptClassification(
        complexity="complex",
        category="analysis",
        estimated_tokens=5000,
        recommended_model="claude-opus-4-6",
        recommended_provider="anthropic",
        confidence=0.95,
        reasoning="long analytical prompt"
    )
    
    router = SmartRouter()
    decision = await router.route(classification, org.id, db, redis_mock)
    assert decision.model in ("claude-opus-4-6", "gpt-4o", "gemini-pro") or "opus" in decision.model or "gpt-4o" in decision.model or "pro" in decision.model


@pytest.mark.asyncio
async def test_org_rule_override(db: AsyncSession):
    """
    Tests custom rule matching overrides.
    """
    org = await org_svc.create_org("Router Org 3", "router-org-3", OrganizationPlan.ENTERPRISE, db)
    redis_mock = AsyncMock()
    redis_mock.get.return_value = None
    
    # Create org rule overriding coding -> claude-sonnet-4-6
    rule = RoutingRule(
        org_id=org.id,
        name="coding override",
        conditions={"category": "coding"},
        target_model="claude-sonnet-4-6",
        target_provider="anthropic",
        priority=1,
        is_active=True
    )
    db.add(rule)
    await db.commit()
    
    from app.routing.prompt_classifier import PromptClassification
    classification = PromptClassification(
        complexity="medium",
        category="coding",
        estimated_tokens=100,
        recommended_model="claude-sonnet-4-6",
        recommended_provider="anthropic",
        confidence=0.88,
        reasoning="coding override match"
    )
    
    router = SmartRouter()
    decision = await router.route(classification, org.id, db, redis_mock)
    assert decision.model == "claude-sonnet-4-6"
    assert "org rule match" in decision.routing_reason


@pytest.mark.asyncio
async def test_fallback_chain(db: AsyncSession):
    """
    Verifies that multi-provider fallback chains are constructed.
    """
    org = await org_svc.create_org("Router Org 4", "router-org-4", OrganizationPlan.ENTERPRISE, db)
    redis_mock = AsyncMock()
    redis_mock.get.return_value = None
    
    from app.routing.prompt_classifier import PromptClassification
    classification = PromptClassification(
        complexity="medium",
        category="chat",
        estimated_tokens=50,
        recommended_model="claude-sonnet-4-6",
        recommended_provider="anthropic",
        confidence=0.8,
        reasoning="normal chat"
    )
    
    router = SmartRouter()
    decision = await router.route(classification, org.id, db, redis_mock)
    
    # Fallback chain should contain alternative provider models (e.g. GPT or Gemini)
    assert len(decision.fallback_chain) >= 1
    # First model shouldn't match secondary models in chain
    assert decision.fallback_chain[0] != (decision.provider, decision.model)


@pytest.mark.asyncio
async def test_savings_calculated(db: AsyncSession):
    """
    Verifies baseline cost is higher than actual cost for simple classification decisions.
    """
    org = await org_svc.create_org("Router Org 5", "router-org-5", OrganizationPlan.ENTERPRISE, db)
    redis_mock = AsyncMock()
    redis_mock.get.return_value = None
    
    from app.routing.prompt_classifier import PromptClassification
    classification = PromptClassification(
        complexity="simple",
        category="chat",
        estimated_tokens=1000,
        recommended_model="claude-haiku-4-5",
        recommended_provider="anthropic",
        confidence=0.9,
        reasoning="simple casual prompt"
    )
    
    router = SmartRouter()
    decision = await router.route(classification, org.id, db, redis_mock)
    
    assert decision.baseline_cost_usd > Decimal("0.00")
    # Haiku should cost less than gpt-4o baseline
    assert decision.estimated_cost_usd < decision.baseline_cost_usd
