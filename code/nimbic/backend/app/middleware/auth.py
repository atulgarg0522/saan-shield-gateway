from fastapi import Header, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.org import Organization
from app.services.key_svc import validate_api_key
from app.services.org_svc import get_org_by_id


async def get_current_api_key(
    authorization: str = Header(...),
    db: AsyncSession = Depends(get_db)
) -> ApiKey:
    """
    Dependency checking the Authorization Bearer header against active DB keys.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header. Expected format: Bearer <key>"
        )

    raw_key = authorization.replace("Bearer ", "").strip()
    api_key = await validate_api_key(raw_key, db)
    
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, revoked, or expired API Key."
        )
    return api_key


async def get_current_org(
    authorization: str = Header(...),
    db: AsyncSession = Depends(get_db)
) -> Organization:
    """
    FastAPI dependency validating the Bearer token and returning the authorized Organization model.
    Raises 401 if invalid.
    """
    api_key = await get_current_api_key(authorization, db)
    
    org = await get_org_by_id(api_key.org_id, db)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found."
        )
    return org


class RequireScope:
    """
    FastAPI dependency factory enforcing scope permissions (raises 403 if scope is deficient).
    """
    def __init__(self, required_scope: str):
        self.required_scope = required_scope

    async def __call__(
        self,
        api_key: ApiKey = Depends(get_current_api_key)
    ) -> ApiKey:
        if self.required_scope not in api_key.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: key lacks required scope: '{self.required_scope}'"
            )
        return api_key
