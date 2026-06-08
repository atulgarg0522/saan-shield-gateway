import uuid
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.provider_config import ProviderConfig
from app.models.request_log import ProviderEnum
from app.services.crypto_svc import encrypt_api_key


async def list_providers(org_id: uuid.UUID, db: AsyncSession) -> List[ProviderConfig]:
    """
    Lists configured providers for an organization.
    """
    stmt = select(ProviderConfig).where(ProviderConfig.org_id == org_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def upsert_provider_config(
    org_id: uuid.UUID,
    provider: ProviderEnum,
    raw_api_key: str,
    base_url: Optional[str],
    db: AsyncSession
) -> ProviderConfig:
    """
    Adds or updates a provider configuration. Encrypts the raw API key using AES-256 before writing to DB.
    """
    # Encrypt raw key
    encrypted_key = encrypt_api_key(raw_api_key)

    # Check if a config for this provider already exists for the org
    stmt = select(ProviderConfig).where(
        ProviderConfig.org_id == org_id,
        ProviderConfig.provider == provider
    )
    result = await db.execute(stmt)
    config = result.scalars().first()

    if config:
        # Update existing config
        config.api_key_encrypted = encrypted_key
        config.base_url = base_url
        config.is_active = True
    else:
        # Create new config
        config = ProviderConfig(
            org_id=org_id,
            provider=provider,
            api_key_encrypted=encrypted_key,
            base_url=base_url,
            is_active=True
        )
        db.add(config)

    await db.commit()
    await db.refresh(config)
    return config


async def delete_provider_config(config_id: uuid.UUID, org_id: uuid.UUID, db: AsyncSession) -> bool:
    """
    Removes a provider config.
    """
    stmt = select(ProviderConfig).where(
        ProviderConfig.id == config_id,
        ProviderConfig.org_id == org_id
    )
    result = await db.execute(stmt)
    config = result.scalars().first()

    if not config:
        return False

    await db.delete(config)
    await db.commit()
    return True
