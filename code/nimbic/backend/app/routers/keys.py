import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.routers.orgs import get_current_admin_api_key
from app.models.api_key import ApiKey
from app.schemas.api_key import (
    KeyCreateRequest,
    KeyUpdateRequest,
    KeyCreateResponse,
    KeyDetailsResponse,
)
from app.services import key_svc

router = APIRouter(prefix="/keys", tags=["API Keys Management"])


@router.post("", response_model=KeyCreateResponse)
async def create_key(
    payload: KeyCreateRequest,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Creates a new secure API Key for the administrator's organization.
    Plaintext raw key is returned exactly once in the response.
    """
    db_key, raw_key = await key_svc.create_api_key(
        org_id=api_key.org_id,
        name=payload.name,
        scopes=payload.scopes,
        db=db,
        expires_at=payload.expires_at
    )
    return {
        "id": db_key.id,
        "name": db_key.name,
        "key_prefix": db_key.key_prefix,
        "raw_key": raw_key,
        "scopes": db_key.scopes,
        "is_active": db_key.is_active,
        "expires_at": db_key.expires_at
    }


@router.get("", response_model=List[KeyDetailsResponse])
async def list_keys(
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Lists all API Keys belonging to the organization.
    """
    stmt = select(ApiKey).where(ApiKey.org_id == api_key.org_id)
    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.patch("/{id}", response_model=KeyDetailsResponse)
async def update_key(
    id: uuid.UUID,
    payload: KeyUpdateRequest,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Updates API Key configuration parameters (name, scopes, active status).
    """
    stmt = select(ApiKey).where(ApiKey.id == id, ApiKey.org_id == api_key.org_id)
    res = await db.execute(stmt)
    key_record = res.scalars().first()

    if not key_record:
        raise HTTPException(status_code=404, detail="API Key not found or access denied.")

    if payload.name is not None:
        key_record.name = payload.name
    if payload.scopes is not None:
        key_record.scopes = payload.scopes
    if payload.is_active is not None:
        key_record.is_active = payload.is_active

    await db.commit()
    await db.refresh(key_record)
    return key_record


@router.delete("/{id}", response_model=KeyDetailsResponse)
async def revoke_key(
    id: uuid.UUID,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Revokes (soft-deletes) an API Key by setting its active status to False.
    """
    stmt = select(ApiKey).where(ApiKey.id == id, ApiKey.org_id == api_key.org_id)
    res = await db.execute(stmt)
    key_record = res.scalars().first()

    if not key_record:
        raise HTTPException(status_code=404, detail="API Key not found or access denied.")

    # Soft delete
    key_record.is_active = False
    await db.commit()
    await db.refresh(key_record)
    return key_record
