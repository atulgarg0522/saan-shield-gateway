import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession
from httpx import AsyncClient

from app.models.org import OrganizationPlan
from app.models.security_policy import PiiActionEnum, PolicyActionEnum
from app.models.security_violation import ViolationTypeEnum, SeverityEnum, ViolationActionEnum, SecurityViolation
from app.services import org_svc, key_svc
from app.services.security_svc import check_security_policies, get_or_create_policy, GeoIPService
from app.db.session import get_db


@pytest.mark.asyncio
async def test_security_policy_lifecycle(db: AsyncSession, client: AsyncClient):
    """
    Verifies that a default security policy is created with an organization,
    and can be retrieved and updated via management API endpoints.
    """
    # 1. Create Organization
    org = await org_svc.create_org("Security Admin Org", "sec-admin-test", OrganizationPlan.FREE, db)
    
    # 2. Verify default policy exists
    policy = await get_or_create_policy(org.id, db)
    assert policy is not None
    assert policy.pii_action == PiiActionEnum.redact
    assert policy.code_action == PolicyActionEnum.warn
    assert policy.sensitive_action == PolicyActionEnum.warn
    assert policy.is_active is True
    
    # 3. Create Admin Key to authenticate API calls
    _, raw_key = await key_svc.create_api_key(
        org_id=org.id,
        name="Admin Security Key",
        scopes=["admin"],
        db=db
    )
    headers = {"Authorization": f"Bearer {raw_key}"}

    # Override database dependency in FastAPI app
    async def override_get_db():
        yield db
    from app.main import app
    app.dependency_overrides[get_db] = override_get_db

    try:
        # 4. Fetch policy via API
        response = await client.get("/api/v1/security/policy", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["pii_action"] == "redact"
        assert data["code_action"] == "warn"
        assert data["sensitive_action"] == "warn"
        
        # 5. Update policy via API
        update_payload = {
            "pii_action": "block",
            "code_action": "block",
            "sensitive_action": "allow",
            "blocked_regions": ["CN", "RU"],
            "custom_patterns": [
                {"name": "API Key Pattern", "regex": r"sk-proj-[a-zA-Z0-9]{20}"}
            ]
        }
        update_response = await client.put("/api/v1/security/policy", json=update_payload, headers=headers)
        assert update_response.status_code == 200
        updated_data = update_response.json()
        assert updated_data["pii_action"] == "block"
        assert updated_data["code_action"] == "block"
        assert updated_data["sensitive_action"] == "allow"
        assert updated_data["blocked_regions"] == ["CN", "RU"]
        assert len(updated_data["custom_patterns"]) == 1
        assert updated_data["custom_patterns"][0]["name"] == "API Key Pattern"
        
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_pii_redaction_and_blocking(db: AsyncSession):
    """
    Tests PII detection, redaction (anonymization) and blocking rules on prompts.
    """
    org = await org_svc.create_org("PII Test Org", "pii-test", OrganizationPlan.FREE, db)
    
    # Text with PII (email, phone, Indian Aadhaar)
    messages = [
        {"role": "user", "content": "Hello, my name is John Doe, email is john.doe@example.com and phone is 9876543210. Aadhaar is 2345 6789 0123."}
    ]
    
    # 1. Test Redact Action (default)
    mutated, is_blocked, block_reason = await check_security_policies(
        org_id=org.id,
        messages=messages,
        provider="openai",
        client_ip="127.0.0.1",
        request_id="req_test_pii_redact",
        db=db
    )
    
    assert is_blocked is False
    assert mutated[0]["content"] != messages[0]["content"]
    assert "john.doe@example.com" not in mutated[0]["content"]
    assert "9876543210" not in mutated[0]["content"]
    assert "2345 6789 0123" not in mutated[0]["content"]
    
    # Confirm DB violation was written
    from sqlalchemy import select
    res = await db.execute(select(SecurityViolation).where(SecurityViolation.org_id == org.id))
    violations = res.scalars().all()
    assert len(violations) > 0
    assert any(v.violation_type == ViolationTypeEnum.pii and v.action_taken == ViolationActionEnum.redacted for v in violations)
    
    # 2. Test Block Action
    policy = await get_or_create_policy(org.id, db)
    policy.pii_action = PiiActionEnum.block
    db.add(policy)
    await db.commit()
    
    mutated_block, is_blocked_block, block_reason_block = await check_security_policies(
        org_id=org.id,
        messages=messages,
        provider="openai",
        client_ip="127.0.0.1",
        request_id="req_test_pii_block",
        db=db
    )
    
    assert is_blocked_block is True
    assert "Personally Identifiable Information" in block_reason_block or "PII" in block_reason_block
    
    # Verify blocked violation logged
    await db.refresh(policy)
    res_b = await db.execute(select(SecurityViolation).where(SecurityViolation.request_id == "req_test_pii_block"))
    v_block = res_b.scalars().first()
    assert v_block is not None
    assert v_block.action_taken == ViolationActionEnum.blocked
    assert v_block.severity == SeverityEnum.critical  # Critical because of Aadhaar number


@pytest.mark.asyncio
async def test_source_code_detection(db: AsyncSession):
    """
    Tests programming source code detection and blocking heuristics.
    """
    org = await org_svc.create_org("Code Test Org", "code-test", OrganizationPlan.FREE, db)
    
    code_prompt = [
        {"role": "user", "content": "Can you explain what this code does?\nimport os\ndef walk_dir(path):\n    for r, d, f in os.walk(path):\n        yield r"}
    ]
    
    # 1. Test Warning Action (default)
    mutated, is_blocked, block_reason = await check_security_policies(
        org_id=org.id,
        messages=code_prompt,
        provider="openai",
        client_ip="127.0.0.1",
        request_id="req_code_warn",
        db=db
    )
    
    assert is_blocked is False
    # Check DB logs
    from sqlalchemy import select
    res = await db.execute(select(SecurityViolation).where(SecurityViolation.request_id == "req_code_warn"))
    violations = res.scalars().all()
    v = next((x for x in violations if x.violation_type == ViolationTypeEnum.source_code), None)
    assert v is not None
    assert v.action_taken == ViolationActionEnum.warned
    assert v.severity in (SeverityEnum.medium, SeverityEnum.high)
    
    # 2. Test Block Action
    policy = await get_or_create_policy(org.id, db)
    policy.code_action = PolicyActionEnum.block
    db.add(policy)
    await db.commit()
    
    mutated_block, is_blocked_block, block_reason_block = await check_security_policies(
        org_id=org.id,
        messages=code_prompt,
        provider="openai",
        client_ip="127.0.0.1",
        request_id="req_code_block",
        db=db
    )
    
    assert is_blocked_block is True
    assert "source code" in block_reason_block.lower()


@pytest.mark.asyncio
async def test_sensitive_content_and_custom_patterns(db: AsyncSession):
    """
    Tests sensitive content flags and org-defined custom regex patterns.
    """
    org = await org_svc.create_org("Sensitive Test Org", "sensitive-test", OrganizationPlan.FREE, db)
    
    # 1. Check standard keywords (NDA term)
    nda_prompt = [{"role": "user", "content": "Summarize this Non-disclosure agreement and balance sheet."}]
    
    mutated, is_blocked, block_reason = await check_security_policies(
        org_id=org.id,
        messages=nda_prompt,
        provider="openai",
        client_ip="127.0.0.1",
        request_id="req_sens_warn",
        db=db
    )
    
    assert is_blocked is False
    from sqlalchemy import select
    res = await db.execute(select(SecurityViolation).where(SecurityViolation.request_id == "req_sens_warn"))
    violations = res.scalars().all()
    v = next((x for x in violations if x.violation_type == ViolationTypeEnum.sensitive_content), None)
    assert v is not None
    assert v.action_taken == ViolationActionEnum.warned
    
    # 2. Check Custom Regex Pattern Match
    policy = await get_or_create_policy(org.id, db)
    policy.custom_patterns = [{"name": "Secret Project Code", "regex": r"Project-[XYZ]"}]
    policy.sensitive_action = PolicyActionEnum.block
    db.add(policy)
    await db.commit()
    
    secret_prompt = [{"role": "user", "content": "Write an update status email for Project-Y."}]
    mutated_block, is_blocked_block, block_reason_block = await check_security_policies(
        org_id=org.id,
        messages=secret_prompt,
        provider="openai",
        client_ip="127.0.0.1",
        request_id="req_sens_block",
        db=db
    )
    
    assert is_blocked_block is True
    assert "sensitive" in block_reason_block.lower()


@pytest.mark.asyncio
async def test_data_residency_checks(db: AsyncSession):
    """
    Tests GeoIP region lookup and data residency policy controls.
    """
    org = await org_svc.create_org("Residency Test Org", "residency-test", OrganizationPlan.FREE, db)
    policy = await get_or_create_policy(org.id, db)
    policy.blocked_regions = ["CN", "RU"]
    policy.allowed_providers_by_region = {"EU": ["azure_openai"]}
    db.add(policy)
    await db.commit()
    
    simple_prompt = [{"role": "user", "content": "Hello!"}]
    
    # Mock GeoIPService to simulate a client from CN (blocked region)
    with patch.object(GeoIPService, "get_country_code", AsyncMock(return_value="CN")):
        mutated, is_blocked, block_reason = await check_security_policies(
            org_id=org.id,
            messages=simple_prompt,
            provider="openai",
            client_ip="1.2.3.4",
            request_id="req_res_cn",
            db=db
        )
        assert is_blocked is True
        assert "country code 'CN' are blocked" in block_reason
        
    # Mock GeoIPService to simulate client from FR (EU country) targeting 'openai' (unallowed provider)
    with patch.object(GeoIPService, "get_country_code", AsyncMock(return_value="FR")):
        # French client trying standard openai (should be blocked as only 'azure_openai' is allowed)
        mutated, is_blocked_eu_openai, block_reason_eu = await check_security_policies(
            org_id=org.id,
            messages=simple_prompt,
            provider="openai",
            client_ip="5.5.5.5",
            request_id="req_res_fr_openai",
            db=db
        )
        assert is_blocked_eu_openai is True
        assert "not allowed for your region" in block_reason_eu
        
        # French client trying azure_openai (should be allowed)
        mutated_ok, is_blocked_eu_azure, block_reason_ok = await check_security_policies(
            org_id=org.id,
            messages=simple_prompt,
            provider="azure_openai",
            client_ip="5.5.5.5",
            request_id="req_res_fr_azure",
            db=db
        )
        assert is_blocked_eu_azure is False


@pytest.mark.asyncio
async def test_security_violations_endpoints_and_stats(db: AsyncSession, client: AsyncClient):
    """
    Verifies that security violations can be listed with filters and aggregated into stats.
    """
    # 1. Create Organization & API Key
    org = await org_svc.create_org("Stats Test Org", "stats-test", OrganizationPlan.FREE, db)
    _, raw_key = await key_svc.create_api_key(
        org_id=org.id,
        name="Stats Key",
        scopes=["admin"],
        db=db
    )
    headers = {"Authorization": f"Bearer {raw_key}"}

    # Override db dependency in main app
    async def override_get_db():
        yield db
    from app.main import app
    app.dependency_overrides[get_db] = override_get_db

    try:
        # Seed some violations
        v1 = SecurityViolation(
            org_id=org.id,
            request_id="req-1",
            violation_type=ViolationTypeEnum.pii,
            severity=SeverityEnum.low,
            action_taken=ViolationActionEnum.redacted,
            details={"entities": [{"type": "EMAIL", "score": 0.9}]},
            prompt_snippet="Hello my email is john@doe.com",
            created_at=datetime(2026, 6, 2, 10, 30, 0)
        )
        v2 = SecurityViolation(
            org_id=org.id,
            request_id="req-2",
            violation_type=ViolationTypeEnum.source_code,
            severity=SeverityEnum.medium,
            action_taken=ViolationActionEnum.warned,
            details={"languages": ["python"]},
            prompt_snippet="def foo(): pass",
            created_at=datetime(2026, 6, 2, 10, 45, 0)
        )
        v3 = SecurityViolation(
            org_id=org.id,
            request_id="req-3",
            violation_type=ViolationTypeEnum.data_residency,
            severity=SeverityEnum.critical,
            action_taken=ViolationActionEnum.blocked,
            details={"country": "CN"},
            prompt_snippet="Hello",
            created_at=datetime(2026, 6, 2, 11, 15, 0)
        )
        db.add_all([v1, v2, v3])
        await db.commit()

        # 2. Get violations list (unfiltered)
        res = await client.get("/api/v1/security/violations", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 3
        assert len(data["items"]) == 3
        
        # 3. Get violations list with filter
        res_filtered = await client.get(
            "/api/v1/security/violations?violation_type=pii",
            headers=headers
        )
        assert res_filtered.status_code == 200
        data_filtered = res_filtered.json()
        assert data_filtered["total"] == 1
        assert data_filtered["items"][0]["request_id"] == "req-1"

        # 4. Get violations list with severity filter
        res_sev = await client.get(
            "/api/v1/security/violations?severity=critical",
            headers=headers
        )
        assert res_sev.status_code == 200
        data_sev = res_sev.json()
        assert data_sev["total"] == 1
        assert data_sev["items"][0]["request_id"] == "req-3"

        # 5. Get statistics
        res_stats = await client.get("/api/v1/security/violations/stats", headers=headers)
        assert res_stats.status_code == 200
        stats = res_stats.json()
        assert stats["total_violations"] == 3
        assert stats["by_type"]["pii"] == 1
        assert stats["by_type"]["source_code"] == 1
        assert stats["by_type"]["residency"] == 1
        assert stats["by_severity"]["critical"] == 1
        assert stats["by_action"]["blocked"] == 1
        assert stats["blocked_requests_pct"] == pytest.approx(33.33, 0.1)
        
        # Verify hour grouping
        assert len(stats["top_violation_hours"]) > 0
        hours = [h["hour"] for h in stats["top_violation_hours"]]
        assert 10 in hours
        assert 11 in hours
        
        # Verify trend grouping
        assert len(stats["trend"]) > 0
        assert stats["trend"][0]["date"] == "2026-06-02"
        assert stats["trend"][0]["count"] == 3

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_policy_dry_run_endpoint(db: AsyncSession, client: AsyncClient):
    """
    Tests the dry-run POST /security/policy/test endpoint.
    """
    org = await org_svc.create_org("Dry Run Org", "dry-run", OrganizationPlan.FREE, db)
    _, raw_key = await key_svc.create_api_key(
        org_id=org.id,
        name="Dry Run Key",
        scopes=["admin"],
        db=db
    )
    headers = {"Authorization": f"Bearer {raw_key}"}

    async def override_get_db():
        yield db
    from app.main import app
    app.dependency_overrides[get_db] = override_get_db

    try:
        policy = await get_or_create_policy(org.id, db)
        policy.blocked_regions = ["CN"]
        db.add(policy)
        await db.commit()

        payload = {
            "prompt": "Hello",
            "provider": "openai",
            "request_ip": "1.2.3.4"
        }
        
        with patch.object(GeoIPService, "get_country_code", AsyncMock(return_value="CN")):
            res = await client.post("/api/v1/security/policy/test", json=payload, headers=headers)
            assert res.status_code == 200
            data = res.json()
            assert data["action"] == "block"
            assert len(data["violations"]) > 0
            assert data["violations"][0]["violation_type"] == "data_residency"

            # Check that no violations were logged in the DB
            from sqlalchemy import select
            violations_res = await db.execute(
                select(SecurityViolation).where(SecurityViolation.org_id == org.id)
            )
            assert len(violations_res.scalars().all()) == 0

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_custom_patterns_endpoints(db: AsyncSession, client: AsyncClient):
    """
    Tests custom pattern POST and DELETE endpoints.
    """
    org = await org_svc.create_org("Patterns Org", "patterns-test", OrganizationPlan.FREE, db)
    _, raw_key = await key_svc.create_api_key(
        org_id=org.id,
        name="Patterns Key",
        scopes=["admin"],
        db=db
    )
    headers = {"Authorization": f"Bearer {raw_key}"}

    async def override_get_db():
        yield db
    from app.main import app
    app.dependency_overrides[get_db] = override_get_db

    try:
        # 1. Add pattern
        payload = {
            "name": "Project Alpha",
            "pattern": r"Alpha-\d{3}",
            "action": "block",
            "description": "Block Project Alpha internal code leak"
        }
        res = await client.post("/api/v1/security/custom-patterns", json=payload, headers=headers)
        assert res.status_code == 200
        patterns = res.json()
        assert len(patterns) == 1
        assert patterns[0]["name"] == "Project Alpha"
        assert patterns[0]["pattern"] == r"Alpha-\d{3}"

        # 2. Add invalid pattern
        invalid_payload = {
            "name": "Invalid Regex",
            "pattern": r"[A-Z{",
            "action": "block"
        }
        res_invalid = await client.post(
            "/api/v1/security/custom-patterns",
            json=invalid_payload,
            headers=headers
        )
        assert res_invalid.status_code == 422

        # 3. Delete pattern
        res_del = await client.delete(
            "/api/v1/security/custom-patterns/Project Alpha",
            headers=headers
        )
        assert res_del.status_code == 200
        patterns_after = res_del.json()
        assert len(patterns_after) == 0

    finally:
        app.dependency_overrides.clear()


# --- PHASE 2 DETECTOR TESTS ---

@pytest.mark.asyncio
async def test_detects_email():
    from app.security.pii_detector import PIIDetector
    detector = PIIDetector()
    result = await detector.analyze(
        text="email me at john@acme.com",
        org_id="test_org",
        custom_patterns=[]
    )
    assert result.has_pii is True
    entities_types = [e.entity_type for e in result.entities]
    assert "EMAIL_ADDRESS" in entities_types or "EMAIL" in entities_types


@pytest.mark.asyncio
async def test_detects_aadhaar():
    from app.security.pii_detector import PIIDetector
    detector = PIIDetector()
    result = await detector.analyze(
        text="my aadhaar is 2345 6789 0123",
        org_id="test_org",
        custom_patterns=[]
    )
    assert result.has_pii is True
    entities_types = [e.entity_type for e in result.entities]
    assert "AADHAAR" in entities_types
    assert result.severity in ("high", "critical")


@pytest.mark.asyncio
async def test_detects_aws_key():
    from app.security.pii_detector import PIIDetector
    detector = PIIDetector()
    result = await detector.analyze(
        text="AKIAIOSFODNN7EXAMPLE",
        org_id="test_org",
        custom_patterns=[]
    )
    assert result.has_pii is True
    entities_types = [e.entity_type for e in result.entities]
    assert "AWS_KEY" in entities_types
    assert result.severity == "critical"


@pytest.mark.asyncio
async def test_detects_private_key():
    from app.security.pii_detector import PIIDetector
    detector = PIIDetector()
    result = await detector.analyze(
        text="-----BEGIN RSA PRIVATE KEY-----",
        org_id="test_org",
        custom_patterns=[]
    )
    assert result.has_pii is True
    entities_types = [e.entity_type for e in result.entities]
    assert "PRIVATE_KEY" in entities_types
    assert result.severity == "critical"


@pytest.mark.asyncio
async def test_clean_prompt():
    from app.security.pii_detector import PIIDetector
    detector = PIIDetector()
    result = await detector.analyze(
        text="Explain the concept of quantum computing.",
        org_id="test_org",
        custom_patterns=[]
    )
    assert result.has_pii is False


@pytest.mark.asyncio
async def test_redaction():
    from app.security.pii_detector import PIIDetector
    detector = PIIDetector()
    result = await detector.analyze(
        text="email me at john@acme.com",
        org_id="test_org",
        custom_patterns=[]
    )
    assert "[EMAIL]" in result.redacted_text


@pytest.mark.asyncio
async def test_detects_python():
    from app.security.code_detector import CodeDetector
    detector = CodeDetector()
    result = await detector.analyze(text="def calculate_revenue(q):")
    assert result.has_code is True
    assert "python" in result.languages


@pytest.mark.asyncio
async def test_detects_sql():
    from app.security.code_detector import CodeDetector
    detector = CodeDetector()
    result = await detector.analyze(text="SELECT * FROM users WHERE id=1")
    assert result.has_code is True
    assert "sql" in result.languages


@pytest.mark.asyncio
async def test_detects_sql_drop():
    from app.security.code_detector import CodeDetector
    detector = CodeDetector()
    result = await detector.analyze(text="DROP TABLE users")
    assert result.has_code is True
    assert "sql" in result.languages
    assert result.severity == "high"


@pytest.mark.asyncio
async def test_clean_text():
    from app.security.code_detector import CodeDetector
    detector = CodeDetector()
    result = await detector.analyze(text="This is a simple paragraph written in plain English explaining project goals.")
    assert result.has_code is False


@pytest.mark.asyncio
async def test_financial():
    from app.security.sensitivity_classifier import SensitivityClassifier
    classifier = SensitivityClassifier()
    result = await classifier.analyze(text="Q3 EBITDA and cap table")
    assert result.is_sensitive is True
    categories_names = [c.name for c in result.categories]
    assert "financial" in categories_names


@pytest.mark.asyncio
async def test_credentials():
    from app.security.sensitivity_classifier import SensitivityClassifier
    classifier = SensitivityClassifier()
    result = await classifier.analyze(text="my password is abc123")
    assert result.is_sensitive is True
    assert result.severity == "critical"


@pytest.mark.asyncio
async def test_clean():
    from app.security.sensitivity_classifier import SensitivityClassifier
    classifier = SensitivityClassifier()
    result = await classifier.analyze(text="How do I configure git configurations on a new machine?")
    assert result.is_sensitive is False


@pytest.mark.asyncio
async def test_block_on_critical_pii(db: AsyncSession):
    from app.security.policy_engine import PolicyEngine
    from app.services.security_svc import get_or_create_policy
    from app.models.security_policy import PiiActionEnum
    from app.services import org_svc
    from app.models.org import OrganizationPlan
    
    org = await org_svc.create_org("PII Block Org", "pii-block-test", OrganizationPlan.FREE, db)
    policy = await get_or_create_policy(org.id, db)
    policy.pii_action = PiiActionEnum.block
    db.add(policy)
    await db.commit()
    
    engine = PolicyEngine()
    decision = await engine.evaluate(
        prompt="Here is my key: -----BEGIN RSA PRIVATE KEY-----",
        request_ip="127.0.0.1",
        provider="openai",
        org_id=org.id,
        db=db,
        redis=None
    )
    assert decision.action == "block"


@pytest.mark.asyncio
async def test_redact_on_pii(db: AsyncSession):
    from app.security.policy_engine import PolicyEngine
    from app.services.security_svc import get_or_create_policy
    from app.models.security_policy import PiiActionEnum
    from app.services import org_svc
    from app.models.org import OrganizationPlan
    
    org = await org_svc.create_org("PII Redact Org", "pii-redact-test", OrganizationPlan.FREE, db)
    policy = await get_or_create_policy(org.id, db)
    policy.pii_action = PiiActionEnum.redact
    db.add(policy)
    await db.commit()
    
    engine = PolicyEngine()
    decision = await engine.evaluate(
        prompt="email me at john@acme.com",
        request_ip="127.0.0.1",
        provider="openai",
        org_id=org.id,
        db=db,
        redis=None
    )
    assert decision.action == "redact"
    assert "[EMAIL]" in decision.final_prompt


@pytest.mark.asyncio
async def test_parallel_detectors(db: AsyncSession):
    from app.security.policy_engine import PolicyEngine
    from app.security.pii_detector import PIIDetector, PIIResult
    from app.security.code_detector import CodeDetector, CodeResult
    from app.security.sensitivity_classifier import SensitivityClassifier, SensitivityResult
    from app.security.residency_enforcer import ResidencyEnforcer, ResidencyResult
    from app.services import org_svc
    from app.models.org import OrganizationPlan
    
    org = await org_svc.create_org("Parallel Mock Org", "parallel-test", OrganizationPlan.FREE, db)
    
    engine = PolicyEngine()
    
    mock_pii_res = PIIResult(has_pii=False, entities=[], redacted_text="mocked pii", severity="low")
    mock_code_res = CodeResult(has_code=False, languages=[], snippets=[], severity="low")
    mock_sens_res = SensitivityResult(is_sensitive=False, categories=[], severity="low", confidence=0.0)
    mock_residency_res = ResidencyResult(is_allowed=True, request_country="US", request_region="US", blocked_reason=None, suggested_provider=None)
    
    with patch.object(PIIDetector, "analyze", AsyncMock(return_value=mock_pii_res)) as mock_pii, \
         patch.object(CodeDetector, "analyze", AsyncMock(return_value=mock_code_res)) as mock_code, \
         patch.object(SensitivityClassifier, "analyze", AsyncMock(return_value=mock_sens_res)) as mock_sens, \
         patch.object(ResidencyEnforcer, "check", AsyncMock(return_value=mock_residency_res)) as mock_residency:
         
        decision = await engine.evaluate(
            prompt="Hello world!",
            request_ip="127.0.0.1",
            provider="openai",
            org_id=org.id,
            db=db,
            redis=None
        )
        
        mock_pii.assert_called_once()
        mock_code.assert_called_once_with("Hello world!")
        mock_sens.assert_called_once()
        mock_residency.assert_called_once()
        assert decision.action == "allow"


