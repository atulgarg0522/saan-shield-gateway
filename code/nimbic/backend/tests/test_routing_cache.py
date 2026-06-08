import pytest
from app.routing.prompt_classifier import PromptClassifier, PromptClassification


@pytest.mark.anyio
async def test_classify_simple_chat():
    classifier = PromptClassifier()
    res = await classifier.classify("Hello! How are you doing today?")
    
    assert isinstance(res, PromptClassification)
    assert res.complexity == "simple"
    assert res.category == "chat"
    assert res.recommended_model == "claude-haiku-4-5"
    assert res.recommended_provider == "anthropic"
    assert "conversational chat" in res.reasoning


@pytest.mark.anyio
async def test_classify_simple_translation():
    classifier = PromptClassifier()
    res = await classifier.classify("Translate 'Good morning' in french")
    
    assert res.complexity == "simple"
    assert res.category == "translation"
    assert res.recommended_model == "claude-haiku-4-5"
    assert "translation" in res.reasoning


@pytest.mark.anyio
async def test_classify_simple_qa():
    classifier = PromptClassifier()
    # Simple general question with no jargon
    res = await classifier.classify("What is the capital of Japan?")
    
    assert res.complexity == "simple"
    assert res.category == "qa"
    assert res.recommended_model == "claude-haiku-4-5"
    assert "without technical domain-specific jargon" in res.reasoning


@pytest.mark.anyio
async def test_classify_medium_coding():
    classifier = PromptClassifier()
    # Matches coding keywords but does not trigger complex conditions, falls in medium
    res = await classifier.classify("Write a python function to add two numbers.")
    
    assert res.complexity == "medium"
    assert res.category == "coding"
    assert res.recommended_model == "claude-sonnet-4-6"
    assert "medium complexity criteria" in res.reasoning


@pytest.mark.anyio
async def test_classify_medium_qa_with_jargon():
    classifier = PromptClassifier()
    # QA category but contains technical jargon, should fall into medium
    res = await classifier.classify("How does the Docker container cache layers?")
    
    assert res.complexity == "medium"
    assert res.category == "qa"
    assert res.recommended_model == "claude-sonnet-4-6"


@pytest.mark.anyio
async def test_classify_complex_phrases():
    classifier = PromptClassifier()
    res = await classifier.classify("Compare and contrast Kubernetes and Docker Swarm.")
    
    assert res.complexity == "complex"
    assert res.recommended_model == "claude-opus-4-6"
    assert "analytical phrasing" in res.reasoning


@pytest.mark.anyio
async def test_classify_complex_math():
    classifier = PromptClassifier()
    # Math category + equations/proof indicator ("=")
    res = await classifier.classify("Solve the equation x^2 - 4 = 0")
    
    assert res.category == "math"
    assert res.complexity == "complex"
    assert res.recommended_model == "claude-opus-4-6"
    assert "Mathematical equation" in res.reasoning


@pytest.mark.anyio
async def test_classify_complex_multiple_questions():
    classifier = PromptClassifier()
    # Contains 3 question marks, triggering complex
    res = await classifier.classify("What is database indexing? Why is it useful? How does it impact writes?")
    
    assert res.complexity == "complex"
    assert res.recommended_model == "claude-opus-4-6"
    assert "multiple questions" in res.reasoning


@pytest.mark.anyio
async def test_classify_complex_token_count():
    classifier = PromptClassifier()
    # Extremely long prompt (over 3000 tokens)
    long_text = "lorem ipsum dolor sit amet " * 800
    res = await classifier.classify(long_text)
    
    assert res.complexity == "complex"
    assert res.estimated_tokens > 3000
    assert res.recommended_model == "claude-opus-4-6"
    assert "High token count" in res.reasoning


# --- SEMANTIC CACHE TESTS ---
from unittest.mock import AsyncMock, MagicMock
from decimal import Decimal
from app.routing.semantic_cache import SemanticCache, CacheResult

class MockRedis:
    def __init__(self):
        self.store = {}
    async def get(self, key):
        return self.store.get(key)
    async def setex(self, key, ttl, value):
        self.store[key] = value

class MockRow:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

class MockResult:
    def __init__(self, row=None):
        self._row = row
    def first(self):
        return self._row


def test_normalize_prompt():
    cache = SemanticCache()
    assert cache.normalize_prompt("  Hello,   World!!!  ") == "hello world"
    assert cache.normalize_prompt("  WHAT   is... AI?  ") == "what is ai"


@pytest.mark.anyio
async def test_lookup_exact_hit():
    cache = SemanticCache()
    redis = MockRedis()
    db = AsyncMock()

    # Seed Redis cache
    normalized = cache.normalize_prompt("Hello Gateway")
    h = cache.hash_prompt(normalized)
    redis_key = f"cache:org123:{h}"
    redis.store[redis_key] = '{"response": "Hello back!", "model": "gpt-4"}'

    res = await cache.lookup("Hello Gateway", "org123", db, redis)
    assert res.hit is True
    assert res.response == "Hello back!"
    assert res.source == "exact"
    assert res.cached_model == "gpt-4"
    assert res.similarity_score == 1.0
    assert isinstance(res.saved_cost_usd, Decimal)


@pytest.mark.anyio
async def test_lookup_semantic_hit():
    cache = SemanticCache()
    redis = MockRedis()
    db = AsyncMock()

    # Mock DB semantic hit (similarity >= 0.92)
    mock_row = MockRow(id="row1", response_text="cached response text", model_used="gpt-4o", similarity=0.95)
    db.execute.return_value = MockResult(mock_row)

    res = await cache.lookup("Some prompt", "org123", db, redis)
    assert res.hit is True
    assert res.response == "cached response text"
    assert res.source == "semantic"
    assert res.similarity_score == 0.95
    assert res.low_confidence is False


@pytest.mark.anyio
async def test_lookup_semantic_soft_hit():
    cache = SemanticCache()
    redis = MockRedis()
    db = AsyncMock()

    # Mock DB semantic soft hit (0.80 <= similarity < 0.92)
    mock_row = MockRow(id="row1", response_text="cached response text", model_used="gpt-4o", similarity=0.85)
    db.execute.return_value = MockResult(mock_row)

    res = await cache.lookup("Some prompt", "org123", db, redis)
    assert res.hit is True
    assert res.response == "cached response text"
    assert res.source == "semantic"
    assert res.similarity_score == 0.85
    assert res.low_confidence is True


@pytest.mark.anyio
async def test_lookup_faq_hit():
    cache = SemanticCache()
    redis = MockRedis()
    db = AsyncMock()

    # Mock DB: first search (embeddings) fails, second search (FAQ) hits (similarity >= 0.88)
    db.execute.side_effect = [
        MockResult(None),  # Prompt embedding miss
        MockResult(MockRow(id="faq1", answer="FAQ Answer", similarity=0.90)),  # FAQ hit
        AsyncMock()  # Update stats execution
    ]

    res = await cache.lookup("FAQ Question", "org123", db, redis)
    assert res.hit is True
    assert res.response == "FAQ Answer"
    assert res.source == "faq"
    assert res.similarity_score == 0.90
    assert res.low_confidence is False


@pytest.mark.anyio
async def test_store_ignores_pii():
    cache = SemanticCache()
    redis = AsyncMock()
    db = AsyncMock()

    await cache.store("prompt with PII", "response", "gpt-4", "org123", db, redis, has_pii=True)

    # Asserts that nothing is written to DB or Redis
    redis.setex.assert_not_called()
    db.execute.assert_not_called()


@pytest.mark.anyio
async def test_store_ignores_long_responses():
    cache = SemanticCache()
    redis = AsyncMock()
    db = AsyncMock()

    long_resp = "a" * 8005
    await cache.store("prompt", long_resp, "gpt-4", "org123", db, redis, has_pii=False)

    redis.setex.assert_not_called()
    db.execute.assert_not_called()


# --- SMART ROUTER TESTS ---
import json
from app.routing.smart_router import SmartRouter, RouteDecision


class MockRulesResult:
    def __init__(self, rows):
        self._rows = rows
    def scalars(self):
        return self
    def all(self):
        return self._rows
    def first(self):
        return self._rows[0] if self._rows else None


@pytest.mark.anyio
async def test_smart_router_complexity_routing():
    router = SmartRouter()
    db = AsyncMock()
    redis = MockRedis()
    
    # Test Simple complexity
    classification = PromptClassification(
        complexity="simple",
        category="chat",
        estimated_tokens=10,
        recommended_model="claude-haiku-4-5",
        recommended_provider="anthropic",
        confidence=0.9,
        reasoning="test"
    )
    
    # Mock active configs (empty list for no active providers override)
    db.execute.return_value = MockRulesResult([])
    
    decision = await router.route(classification, "org123", db, redis)
    assert decision.provider == "anthropic"
    assert decision.model == "claude-haiku-4-5"
    assert ("anthropic", "claude-sonnet-4-6") in decision.fallback_chain
    assert decision.baseline_cost_usd > 0


@pytest.mark.anyio
async def test_smart_router_org_rule_match():
    router = SmartRouter()
    db = AsyncMock()
    redis = MockRedis()
    
    # Set up cached rules in Redis for "org123"
    redis_key = "routing_rules:org123"
    rules = [
        {
            "name": "Coding Rule",
            "conditions": {"category": "coding"},
            "target_model": "gpt-4o-mini",
            "target_provider": "openai",
            "priority": 1
        }
    ]
    redis.store[redis_key] = json.dumps(rules)
    
    classification = PromptClassification(
        complexity="medium",
        category="coding",
        estimated_tokens=50,
        recommended_model="claude-sonnet-4-6",
        recommended_provider="anthropic",
        confidence=0.9,
        reasoning="test"
    )
    
    # Mock active configs (empty list for no active providers override)
    db.execute.return_value = MockRulesResult([])
    
    decision = await router.route(classification, "org123", db, redis)
    assert decision.provider == "openai"
    assert decision.model == "gpt-4o-mini"
    assert decision.routing_reason == "org rule match: Coding Rule"


# --- PROXY INTEGRATION & FALLBACK RETRIES TESTS ---
from app.services.proxy_svc import execute_proxy, ProxyRequest, ProxyResult, ProviderTimeoutError
from app.models.request_log import ProviderEnum

@pytest.mark.anyio
async def test_execute_proxy_cache_hit():
    db = AsyncMock()
    redis = MockRedis()
    
    # Seed Redis cache to trigger a cache hit
    prompt = "Hello"
    normalized = "hello"
    import hashlib
    prompt_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    redis_key = f"cache:org123:{prompt_hash}"
    redis.store[redis_key] = json.dumps({"response": "Hi there!", "model": "gpt-4o"})
    
    request = ProxyRequest(
        org_id="org123",
        api_key_id="key123",
        provider=ProviderEnum.openai,
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        stream=False,
        extra_params={},
        client_ip="127.0.0.1",
        request_id="req123"
    )
    
    # Mock policy engine (allow action)
    with pytest.MonkeyPatch.context() as mp:
        mock_policy = AsyncMock()
        mock_decision = MagicMock()
        mock_decision.action = "allow"
        mock_decision.final_prompt = prompt
        mock_decision.should_log_violation = False
        mock_decision.violations = []
        mock_policy.evaluate.return_value = mock_decision
        
        mp.setattr("app.security.policy_engine.PolicyEngine.evaluate", mock_policy.evaluate)
        
        # Monkeypatch redis client to use MockRedis
        mp.setattr("app.redis.redis_client", redis)
        db.execute.return_value = MockRulesResult([])
        
        # Mock FinOps attribution and budget check to prevent DB queries on Mock session
        from app.finops.attribution_service import Attribution, BudgetCheck
        async def mock_resolve_attribution(*args, **kwargs):
            return Attribution()
        async def mock_check_budget(*args, **kwargs):
            return BudgetCheck(allowed=True)
        mp.setattr("app.finops.attribution_service.resolve_attribution", mock_resolve_attribution)
        mp.setattr("app.finops.attribution_service.check_budget", mock_check_budget)
        
        result = await execute_proxy(request, db)
        
        assert result.status_code == 200
        assert result.response_body["choices"][0]["message"]["content"] == "Hi there!"
        assert result.actual_provider == "cache"
        assert result.actual_model == "gpt-4o"


@pytest.mark.anyio
async def test_execute_proxy_fallback_success():
    db = AsyncMock()
    redis = MockRedis()
    
    request = ProxyRequest(
        org_id="org123",
        api_key_id="key123",
        provider=ProviderEnum.anthropic,
        model="claude-haiku-4-5",
        messages=[{"role": "user", "content": "Hello"}],
        stream=False,
        extra_params={},
        client_ip="127.0.0.1",
        request_id="req123"
    )
    
    # Mock PolicyEngine, PromptClassifier, and SmartRouter
    with pytest.MonkeyPatch.context() as mp:
        mock_policy = AsyncMock()
        mock_policy_decision = MagicMock()
        mock_policy_decision.action = "allow"
        mock_policy_decision.final_prompt = "Hello"
        mock_policy_decision.should_log_violation = False
        mock_policy_decision.violations = []
        mock_policy.evaluate.return_value = mock_policy_decision
        mp.setattr("app.security.policy_engine.PolicyEngine.evaluate", mock_policy.evaluate)
        
        # Classifier
        mock_classifier = AsyncMock()
        mock_classification = PromptClassification(
            complexity="simple",
            category="chat",
            estimated_tokens=5,
            recommended_model="claude-haiku-4-5",
            recommended_provider="anthropic",
            confidence=0.9,
            reasoning="test"
        )
        mock_classifier.classify.return_value = mock_classification
        mp.setattr("app.routing.prompt_classifier.PromptClassifier.classify", mock_classifier.classify)
        
        # SmartRouter
        mock_router = AsyncMock()
        mock_route_decision = RouteDecision(
            provider="anthropic",
            model="claude-haiku-4-5",
            fallback_chain=[("openai", "gpt-4o-mini")],
            routing_reason="test",
            estimated_cost_usd=Decimal("0.0001"),
            baseline_cost_usd=Decimal("0.0005")
        )
        mock_router.route.return_value = mock_route_decision
        mp.setattr("app.routing.smart_router.SmartRouter.route", mock_router.route)
        
        # Mock semantic cache lookup queries:
        # 1. PromptEmbedding select -> MockResult(None)
        # 2. FAQ select -> MockResult(None)
        # 3. Routing rules select -> MockRulesResult([])
        # 4. Active configs select -> MockRulesResult([])
        db.execute.side_effect = [
            MockResult(None),      # Prompt embedding miss
            MockResult(None),      # FAQ miss
            MockRulesResult([]),   # Routing rules query
            MockRulesResult([])    # Active configs query
        ]
        
        # Monkeypatch redis client to use MockRedis
        mp.setattr("app.redis.redis_client", redis)
        
        # Mock FinOps attribution and budget check to prevent DB queries on Mock session
        from app.finops.attribution_service import Attribution, BudgetCheck
        async def mock_resolve_attribution(*args, **kwargs):
            return Attribution()
        async def mock_check_budget(*args, **kwargs):
            return BudgetCheck(allowed=True)
        mp.setattr("app.finops.attribution_service.resolve_attribution", mock_resolve_attribution)
        mp.setattr("app.finops.attribution_service.check_budget", mock_check_budget)
        
        # Call provider mock
        call_count = 0
        async def mock_call_provider(provider_str, model, prompt, req, session_db):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Primary fails with timeout
                raise ProviderTimeoutError("Connection timed out")
            else:
                # Fallback succeeds
                return ProxyResult(
                    response_body={"choices": [{"message": {"role": "assistant", "content": "Fallback success!"}}]},
                    prompt_tokens=5,
                    completion_tokens=5,
                    cost_usd=Decimal("0.0001"),
                    latency_ms=150,
                    status_code=200,
                    actual_provider=provider_str,
                    actual_model=model
                )
                
        mp.setattr("app.services.proxy_svc.call_provider", mock_call_provider)
        
        # Stub background logs to avoid DB session calls in unit testing
        async def mock_log_cost_savings_bg(*args, **kwargs):
            pass
        mp.setattr("app.services.proxy_svc.log_cost_savings_bg", mock_log_cost_savings_bg)
        
        result = await execute_proxy(request, db)
        
        assert result.status_code == 200
        assert result.response_body["choices"][0]["message"]["content"] == "Fallback success!"
        assert result.actual_provider == "openai"
        assert result.actual_model == "gpt-4o-mini"
        assert call_count == 2



