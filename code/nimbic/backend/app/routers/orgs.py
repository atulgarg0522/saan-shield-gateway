from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.schemas.org import OrgCreate, OrgUpdate, OrgResponse
from app.services import org_svc
from app.routers.proxy import get_current_api_key
from app.models.api_key import ApiKey

router = APIRouter(prefix="/org", tags=["Organization Management"])


async def get_current_admin_api_key(api_key: ApiKey = Depends(get_current_api_key)) -> ApiKey:
    """
    Dependency checking that the authorized API key carries 'admin' scopes.
    """
    if "admin" not in api_key.scopes:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Admin credentials required to access management endpoints."
        )
    return api_key


@router.get("", response_model=OrgResponse)
async def get_organization(
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves information for the authorized administrator's organization.
    """
    org = await org_svc.get_org_by_id(api_key.org_id, db)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found.")
    return org


@router.post("", response_model=OrgResponse)
async def onboard_organization(
    payload: OrgCreate,
    db: AsyncSession = Depends(get_db)
):
    """
    Initial onboarding endpoint to register a new organization.
    """
    try:
        org = await org_svc.create_org(payload.name, payload.slug, payload.plan, db)
        return org
    except Exception as e:
        # Catch duplicate slug exceptions
        raise HTTPException(status_code=400, detail=f"Organization registration failed: {str(e)}")


@router.patch("", response_model=OrgResponse)
async def update_organization(
    payload: OrgUpdate,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Updates the organization's settings (name and billing plan).
    """
    org = await org_svc.update_org(api_key.org_id, payload.name, payload.plan, db)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found.")
    return org
