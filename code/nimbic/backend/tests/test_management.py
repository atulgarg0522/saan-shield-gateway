import pytest
import uuid
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.org import OrganizationPlan
from app.models.request_log import ProviderEnum
from app.services import org_svc, provider_svc, key_svc, analytics_svc


@pytest.mark.asyncio
async def test_organization_onboarding_and_updates(db: AsyncSession):
    """
    Verifies organization onboarding, listing, and updates.
    """
    # 1. Onboard Org
    org_name = "Acme Intelligence"
    org_slug = "acme-intel"
    org = await org_svc.create_org(org_name, org_slug, OrganizationPlan.FREE, db)
    
    assert org.id is not None
    assert org.name == org_name
    assert org.slug == org_slug
    assert org.plan == OrganizationPlan.FREE

    # 2. Update Org Settings
    updated = await org_svc.update_org(org.id, "Acme Inc.", OrganizationPlan.ENTERPRISE, db)
    assert updated is not None
    assert updated.name == "Acme Inc."
    assert updated.plan == OrganizationPlan.ENTERPRISE


@pytest.mark.asyncio
async def test_provider_credentials_aes_envelope(db: AsyncSession):
    """
    Verifies provider configurations are saved, encrypted, listed, and deleted securely.
    """
    # Setup Org
    org = await org_svc.create_org("Credential Test Org", "cred-test", OrganizationPlan.FREE, db)

    # 1. Upsert Provider Config
    config = await provider_svc.upsert_provider_config(
        org_id=org.id,
        provider=ProviderEnum.OPENAI,
        raw_api_key="sk-test-key-payload-12345",
        base_url="https://api.openai.com/v1",
        db=db
    )

    assert config.id is not None
    assert config.provider == ProviderEnum.OPENAI
    assert config.base_url == "https://api.openai.com/v1"
    assert config.api_key_encrypted != "sk-test-key-payload-12345"

    # 2. List Configs (verifies keys exist but are hidden)
    configs = await provider_svc.list_providers(org.id, db)
    assert len(configs) == 1
    assert configs[0].provider == ProviderEnum.OPENAI

    # 3. Delete Config
    deleted = await provider_svc.delete_provider_config(config.id, org.id, db)
    assert deleted is True

    configs_after = await provider_svc.list_providers(org.id, db)
    assert len(configs_after) == 0


@pytest.mark.asyncio
async def test_api_key_lifecycle_and_admin_scopes(db: AsyncSession):
    """
    Verifies that API keys are generated and validated, and asserts
    that scopes (specifically 'admin') restrict/grant authentication.
    """
    org = await org_svc.create_org("Auth Test Org", "auth-test", OrganizationPlan.FREE, db)

    # 1. Create Key with Admin Scopes
    db_key, raw_key = await key_svc.create_api_key(
        org_id=org.id,
        name="Primary Admin Key",
        scopes=["admin", "proxy"],
        db=db
    )

    assert db_key.id is not None
    assert db_key.key_prefix.startswith("nim_")
    assert raw_key.startswith(db_key.key_prefix)
    assert "admin" in db_key.scopes

    # 2. Validate Key
    validated = await key_svc.validate_api_key(raw_key, db)
    assert validated is not None
    assert validated.id == db_key.id
    assert validated.name == "Primary Admin Key"
    assert "admin" in validated.scopes

    # 3. Create key WITHOUT Admin Scopes
    _, raw_proxy_key = await key_svc.create_api_key(
        org_id=org.id,
        name="Proxy Only Key",
        scopes=["proxy"],
        db=db
    )

    validated_proxy = await key_svc.validate_api_key(raw_proxy_key, db)
    assert validated_proxy is not None
    assert "admin" not in validated_proxy.scopes
