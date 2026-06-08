import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.org import OrganizationPlan
from app.models.security_policy import PiiActionEnum, PolicyActionEnum
from app.models.security_violation import SecurityViolation, ViolationTypeEnum, ViolationActionEnum, SeverityEnum
from app.services import org_svc
from app.security.residency_enforcer import ResidencyEnforcer, ResidencyResult
from app.security.policy_engine import PolicyEngine


@pytest.mark.asyncio
async def test_residency_enforcer_rules(db: AsyncSession):
    org = await org_svc.create_org("Enforcer Org", "enf-org", OrganizationPlan.FREE, db)
    
    # Fetch seeded policy and configure
    from app.services.security_svc import get_or_create_policy
    policy = await get_or_create_policy(org.id, db)
    policy.blocked_regions = ["CN"]
    policy.allowed_providers_by_region = {"EU": ["azure_openai"]}
    db.add(policy)
    await db.commit()

    enforcer = ResidencyEnforcer()

    # 1. Test allowed request
    res_ok = await enforcer.check("127.0.0.1", "openai", str(org.id), policy)
    assert res_ok.is_allowed is True

    # 2. Test blocked country
    with patch.object(ResidencyEnforcer, "ensure_db", AsyncMock()):
        mock_reader = MagicMock()
        mock_country_res = MagicMock()
        mock_country_res.country.iso_code = "CN"
        mock_country_res.continent.code = "AS"
        mock_reader.country.return_value = mock_country_res
        
        with patch.object(ResidencyEnforcer, "_get_reader", return_value=mock_reader):
            res_blocked = await enforcer.check("1.2.3.4", "openai", str(org.id), policy)
            assert res_blocked.is_allowed is False
            assert "country code 'CN' are blocked" in res_blocked.blocked_reason

    # 3. Test allowed provider override
    with patch.object(ResidencyEnforcer, "ensure_db", AsyncMock()):
        mock_reader = MagicMock()
        mock_country_res = MagicMock()
        mock_country_res.country.iso_code = "FR"
        mock_country_res.continent.code = "EU"
        mock_reader.country.return_value = mock_country_res
        
        with patch.object(ResidencyEnforcer, "_get_reader", return_value=mock_reader):
            # EU client using standard 'openai' should be blocked
            res_eu_blocked = await enforcer.check("5.5.5.5", "openai", str(org.id), policy)
            assert res_eu_blocked.is_allowed is False
            assert res_eu_blocked.suggested_provider == "azure_openai"
            
            # EU client using 'azure_openai' should be allowed
            res_eu_ok = await enforcer.check("5.5.5.5", "azure_openai", str(org.id), policy)
            assert res_eu_ok.is_allowed is True


@pytest.mark.asyncio
async def test_policy_engine_evaluation(db: AsyncSession):
    org = await org_svc.create_org("Engine Org", "engine-org", OrganizationPlan.FREE, db)
    
    engine = PolicyEngine()
    
    # Verify policy defaults: PII redact, Code warn, Sensitive warn
    # 1. Evaluate clean prompt
    dec_ok = await engine.evaluate(
        prompt="Tell me a joke about computer systems.",
        request_ip="127.0.0.1",
        provider="openai",
        org_id=org.id,
        db=db,
        redis=None,
        request_id="req_clean"
    )
    assert dec_ok.action == "allow"
    assert dec_ok.final_prompt == "Tell me a joke about computer systems."
    assert len(dec_ok.violations) == 0

    # 2. Evaluate prompt containing PII (scanned and redacted)
    dec_pii = await engine.evaluate(
        prompt="My name is John Doe, email john.doe@example.com",
        request_ip="127.0.0.1",
        provider="openai",
        org_id=org.id,
        db=db,
        redis=None,
        request_id="req_pii"
    )
    assert dec_pii.action == "redact"
    assert "john.doe@example.com" not in dec_pii.final_prompt
    assert "[EMAIL]" in dec_pii.final_prompt
    assert len(dec_pii.violations) == 1
    assert dec_pii.violations[0].violation_type == "pii"
    assert dec_pii.violations[0].action_applied == "redacted"
    
    # Save violations to DB for test validation
    for v in dec_pii.violations:
        violation_db = SecurityViolation(
            org_id=org.id,
            request_id="req_pii",
            violation_type=ViolationTypeEnum(v.violation_type),
            severity=SeverityEnum(v.severity),
            action_taken=ViolationActionEnum(v.action_applied),
            details=v.details,
            prompt_snippet="My name is John Doe, email john.doe@example.com"
        )
        db.add(violation_db)
    await db.commit()
    
    # Confirm written to DB
    res = await db.execute(select(SecurityViolation).where(SecurityViolation.request_id == "req_pii"))
    violation_record = res.scalars().first()
    assert violation_record is not None
    assert violation_record.violation_type == ViolationTypeEnum.pii
    assert violation_record.action_taken == ViolationActionEnum.redacted
