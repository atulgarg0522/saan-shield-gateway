import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.routers.orgs import get_current_admin_api_key
from app.models.api_key import ApiKey
from app.schemas.provider import ProviderConfigRequest, ProviderConfigResponse
from app.services import provider_svc

router = APIRouter(prefix="/providers", tags=["Provider Credentials Management"])


@router.get("", response_model=List[ProviderConfigResponse])
async def list_configurations(
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Lists configured provider credentials metadata for the organization.
    Decrypted credentials are never exposed in responses.
    """
    configs = await provider_svc.list_providers(api_key.org_id, db)
    return configs


@router.post("", response_model=ProviderConfigResponse)
async def configure_provider(
    payload: ProviderConfigRequest,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Adds or updates a provider configuration. Encrypts the raw API key securely using AES-256 before writing to the database.
    """
    try:
        config = await provider_svc.upsert_provider_config(
            org_id=api_key.org_id,
            provider=payload.provider,
            raw_api_key=payload.api_key,
            base_url=payload.base_url,
            db=db
        )
        return config
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Credential configuration failed: {str(e)}"
        )


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_configuration(
    id: uuid.UUID,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Removes a provider credential configuration from the organization.
    """
    deleted = await provider_svc.delete_provider_config(id, api_key.org_id, db)
    if not deleted:
        raise HTTPException(status_code=404, detail="Provider configuration not found or access denied.")
    return None
